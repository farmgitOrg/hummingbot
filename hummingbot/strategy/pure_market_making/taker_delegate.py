import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from hummingbot.core.data_type.common import TradeType
from hummingbot.model.inventory_cost import InventoryCost
from hummingbot.model.sql_connection_manager import SQLConnectionManager
from hummingbot.strategy.maker_taker_market_pair import MakerTakerMarketPair

from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    BuyOrderCreatedEvent,
    MarketEvent,
    MarketOrderFailureEvent,
    OrderCancelledEvent,
    OrderFilledEvent,
    SellOrderCompletedEvent,
    SellOrderCreatedEvent,
    OrderExpiredEvent
)

s_decimal_0 = Decimal("0")
pmm_taker_delegate_logger = None

class TakerDelegate:
    @classmethod
    def logger(cls):
        global pmm_taker_delegate_logger
        if pmm_taker_delegate_logger is None:
            pmm_taker_delegate_logger = logging.getLogger(__name__)
        return pmm_taker_delegate_logger

    def log_with_clock(self, log_level: int, msg: str, **kwargs):
        clock_timestamp = pd.Timestamp(self._current_timestamp, unit="s", tz="UTC")
        self.logger().log(log_level, f"{msg} [clock={str(clock_timestamp)}]", **kwargs)

    # def __init__(self, strategy: PureMarketMakingStrategy, market_pairs: MakerTakerMarketPair) -> None:
    def __init__(self, market_pairs: MakerTakerMarketPair, check_hedge_interval_sec:Decimal, hedge_amount_threshold:Decimal) -> None:
        self._maker_market = market_pairs.maker.market
        self._taker_market = market_pairs.taker.market
        self._market_pairs = market_pairs
        # self._strategy = strategy
        self._check_hedge_interval = check_hedge_interval_sec
        self._hedge_amount_threshold = hedge_amount_threshold
        self._maker_order_id_to_filled_trades = {}
        self._maker_filled_trades_set:set = set()
        self._maker_filled_events_set:set = set()
        self._hedging_ongoing_record_set:set = set()
        self._hedging_taker_order_id_to_taker_filled_trades = {} # taker order id -> taker filled trades events
        self._hedging_taker_order_id_to_maker_filled_trades = {}  # taker order id -> 所对冲的maker filled trades events

    def debug(self):
        # buy_price = self._taker_market.get_price(self._market_pairs.taker.trading_pair, True)
        # sell_price = self._taker_market.get_price(self._market_pairs.taker.trading_pair, False)
        buy_price = self._market_pairs.taker.get_price(True)
        sell_price = self._market_pairs.taker.get_price(False)
        self.logger().warning(f"##@@## buy/sell price is : {buy_price} / {sell_price}, diff: {sell_price - buy_price}")
        
    def get_taker_price(self, base_size:Decimal, is_buy:bool) -> Decimal:
        # taker_price = self._taker_market.get_price_for_volume(self._market_pairs.taker.trading_pair,
        #                                                     is_buy,
        #                                                     base_size.result_price)
        taker_price = self._market_pairs.taker.get_price_for_volume(is_buy, base_size.result_price)
        return taker_price

    def need_do_hedge(self) -> bool:
        return True

    def check_and_process_hedge(self):
        maker_buy_filled_amount = Decimal(0)
        maker_sell_filled_amount = Decimal(0)
        maker_buy_filled_volume = Decimal(0)
        maker_sell_filled_volume = Decimal(0)
        
        for event in self._maker_filled_events_set:
            if event.tradde_type is TradeType.BUY:
                maker_buy_filled_amount += event.amount
                maker_buy_filled_volume += event.amount * event.price
            else:
                maker_sell_filled_amount += event.amount
                maker_sell_filled_volume += event.amount * event.price

        self.log_with_clock(
            logging.WARN,
            f"check_and_process_hedge: maker_buy_filled_amount: {maker_buy_filled_amount} @ avgprice {maker_buy_filled_volume/maker_buy_filled_amount} "
            f"maker_sell_filled_amount: {maker_sell_filled_amount} @ avgprice {maker_sell_filled_volume/maker_sell_filled_amount}"
        )
        #update the event record beforehand, to avoid any blocking ops later
        self._hedging_ongoing_record_set = self._maker_filled_events_set  # FIXME: handle _hedging_ongoing_record_set not empty case
        self._maker_filled_events_set = {}
    
        order_id = None
        maker_unbalanced_amount = maker_buy_filled_amount - maker_sell_filled_amount
        if maker_unbalanced_amount > self._hedge_amount_threshold: # buy amount > sell amount on maker, sell on taker market
            amount = maker_unbalanced_amount
            self.log_with_clock(
                logging.WARN,
                f"({self.trading_pair}) maker_unbalanced_amount {amount} > " 
                f"threshold {self._hedge_amount_threshold} , taker SELL amount {amount}"
            )
            sell_price = self.get_taker_price(maker_unbalanced_amount, False)
            try:
                order_id = self.sell_with_specific_market(self._market_pairs.taker, amount,
                                                         order_type=order_type, price=sell_price,
                                                         expiration_seconds=expiration_seconds) # TODO: 60sec for CEX
            except ValueError as e:
                self.logger().warning(f"taker_delegate: Placing a taker SELL order on market {str(self._market_pairs.taker.market.name)} "
                                      f"failed with the following error: {str(e)}")

        elif maker_unbalanced_amount <  -1*self._hedge_amount_threshold: # buy amount < sell amount on maker, buy on taker market
            amount = -maker_unbalanced_amount
            self.log_with_clock(
                logging.WARN,
                f"taker_delegate: maker_unbalanced_amount {amount} > " 
                f"threshold {self._hedge_amount_threshold} , taker BUY amount {amount}"
            )
            buy_price = self.get_taker_price(amount, True)
            try:
                order_id = self.buy_with_specific_market(self._market_pairs.taker, amount,
                                                         order_type=order_type, price=buy_price,
                                                         expiration_seconds=expiration_seconds) # TODO: 60sec for CEX
            except ValueError as e:
                self.logger().warning(f"taker_delegate: Placing a taker BUY order on market {str(self._market_pairs.taker.market.name)} "
                                      f"failed with the following error: {str(e)}")
        else:
            self.log_with_clock(
                logging.WARN,
                f"taker_delegate: maker_unbalanced_amount {maker_unbalanced_amount} < " 
                f"threshold {self._hedge_amount_threshold} , skip hedging"
            )

            #FIXME: handle failed order remain amount

        if order_id is None:
            #recover event record if any error
            self._maker_filled_events_set = self._maker_filled_events_set | self._hedging_ongoing_record_set
            self._hedging_ongoing_record_set = {}
            return
        
        self._hedging_taker_order_id_to_taker_filled_trades[order_id] = []
        self._hedging_taker_order_id_to_maker_filled_trades[order_id] = self._hedging_ongoing_record_set
        return order_id

    def did_create_buy_order(self):
        return
    def did_create_sell_order(self):
        return

    def did_cancel_order(self, order_canceled_event: OrderCancelledEvent):
        order_id:str = order_canceled_event.order_id
        if order_id in self._hedging_taker_order_id_to_taker_filled_trades.keys():
            self.log_with_clock(
                logging.ERROR,
                f"taker_delegate: did_cancel_order {order_id}"
            )
            del self._hedging_taker_order_id_to_taker_filled_trades[order_id]

    def did_fail_order(self, order_failed_event: MarketOrderFailureEvent):
        order_id:str = order_failed_event.order_id
        if order_id in self._hedging_taker_order_id_to_taker_filled_trades.keys():
            self.log_with_clock(
                logging.ERROR,
                f"taker_delegate: did_fail_order {order_id}"
            )
            del self._hedging_taker_order_id_to_taker_filled_trades[order_id] #FIXME: partial filled

    def did_expire_order(self, order_expired_event: OrderExpiredEvent):
        order_id:str = order_expired_event.order_id
        if order_id in self._hedging_taker_order_id_to_taker_filled_trades.keys():
            self.log_with_clock(
                logging.ERROR,
                f"taker_delegate: did_expire_order {order_id}"
            )
            del self._hedging_taker_order_id_to_taker_filled_trades[order_id] #FIXME: partial filled

    def did_complete_buy_order(self, order_completed_event: BuyOrderCompletedEvent):
        order_id:str = order_completed_event.order_id
        if order_id in self._hedging_taker_order_id_to_taker_filled_trades.keys():
            self.log_with_clock(
                logging.ERROR,
                f"taker_delegate: did_complete_buy_order {order_id}"
            )
            del self._hedging_taker_order_id_to_taker_filled_trades[order_id]
            del self._hedging_taker_order_id_to_maker_filled_trades[order_id]

    def did_complete_sell_order(self, order_completed_event: SellOrderCompletedEvent):
        order_id:str = order_completed_event.order_id
        if order_id in self._hedging_taker_order_id_to_taker_filled_trades.keys():
            self.log_with_clock(
                logging.ERROR,
                f"taker_delegate: did_complete_sell_order {order_id}"
            )
            del self._hedging_taker_order_id_to_taker_filled_trades[order_id]
            del self._hedging_taker_order_id_to_maker_filled_trades[order_id]

    #prerequisite: the order of the event really belongs to us.
    def did_fill_maker_order(self, order_filled_event: OrderFilledEvent):
        order_id:str = order_filled_event.order_id
        exchange_trade_id:str = order_filled_event.exchange_trade_id
        
        # if order_id in self._maker_order_id_to_filled_trades.keys():
        #     self._maker_order_id_to_filled_trades[order_id].append(exchange_trade_id)
        #     self._maker_filled_trade_to_event[exchange_trade_id] = order_filled_event

        self._maker_filled_trades_set.add(exchange_trade_id)
        self._maker_filled_events_set.add(order_filled_event)

        #FIXME: need update inventory after taker!!

        return

    def did_fill_taker_order(self, order_filled_event: OrderFilledEvent):
        order_id:str = order_filled_event.order_id
        exchange_trade_id:str = order_filled_event.exchange_trade_id
        
        if order_id in self._hedging_taker_order_id_to_taker_filled_trades.keys():
            self._hedging_taker_order_id_to_taker_filled_trades[order_id].append(order_filled_event)
        return
