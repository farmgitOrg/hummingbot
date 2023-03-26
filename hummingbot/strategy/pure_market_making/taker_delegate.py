import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.model.inventory_cost import InventoryCost
from hummingbot.model.sql_connection_manager import SQLConnectionManager
from hummingbot.strategy.maker_taker_market_pair import MakerTakerMarketPair

s_decimal_0 = Decimal("0")
pmm_taker_delegate_logger = None

class TakerDelegate:
    @classmethod
    def logger(cls):
        global pmm_taker_delegate_logger
        if pmm_taker_delegate_logger is None:
            pmm_taker_delegate_logger = logging.getLogger(__name__)
        return pmm_taker_delegate_logger
    
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
        self._hedging_taker_order_id_to_filled_trade_id = {}

    def debug(self):
        buy_price = self._taker_market.get_price(self._market_pairs.taker.trading_pair, True)
        sell_price = self._taker_market.get_price(self._market_pairs.taker.trading_pair, False)
        self.logger().warning(f"##@@## buy/sell price is : {buy_price} / {sell_price}, diff: {sell_price - buy_price}")
        
    def get_taker_price(self, base_size:Decimal, is_buy:bool) -> Decimal:
        taker_price = self._taker_market.get_price_for_volume(self._market_pairs.taker.trading_pair,
                                                            is_buy,
                                                            base_size.result_price)
        return taker_price

    def need_do_hedge(self) -> Bool:
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
            f"({self.trading_pair}) maker_buy_filled_amount: {maker_buy_filled_amount} @ avgprice {maker_buy_filled_volume/maker_buy_filled_amount} "
            f"maker_sell_filled_amount: {maker_sell_filled_amount} @ avgprice {maker_sell_filled_volume/maker_sell_filled_amount}"
        )

        maker_unbalanced_amount = maker_buy_filled_amount - maker_sell_filled_amount
        if maker_unbalanced_amount > self._hedge_amount_threshold:
            self.log_with_clock(
                logging.WARN,
                f"({self.trading_pair}) maker_unbalanced_amount {maker_unbalanced_amount} > " 
                f"threshold {self._hedge_amount_threshold} , taker SELL amount {maker_unbalanced_amount}"
            )
            sell_price = self.get_taker_price(maker_unbalanced_amount, False)
            
        elif maker_unbalanced_amount <  -1*self._hedge_amount_threshold:
            self.log_with_clock(
                logging.WARN,
                f"({self.trading_pair}) maker_unbalanced_amount {-maker_unbalanced_amount} > " 
                f"threshold {self._hedge_amount_threshold} , taker BUY amount {-maker_unbalanced_amount}"
            )
            buy_price = self.get_taker_price(-1*maker_unbalanced_amount, True)
        else:
            return
    #     #FIXME: handle failed order remain amount


        if is_buy:
            try:
                order_id = self.buy_with_specific_market(market_info, amount,
                                                         order_type=order_type, price=price,
                                                         expiration_seconds=expiration_seconds)
            except ValueError as e:
                self.logger().warning(f"Placing an order level {order_level} on market {str(market_info.market.name)} "
                                      f"failed with the following error: {str(e)}")
        else:
            try:
                order_id = self.sell_with_specific_market(market_info, amount,
                                                          order_type=order_type, price=price,
                                                          expiration_seconds=expiration_seconds)
            except ValueError as e:
                self.logger().warning(f"Placing an order level {order_level} on market {str(market_info.market.name)} "
                                      f"failed with the following error: {str(e)}")
        if order_id is None:
            return
        self.log_with_clock(logging.DEBUG, f"place_order: order_id {str(order_id)}")



        




        return

    def did_create_buy_order(self):
        return
    def did_create_sell_order(self):
        return

    #prerequisite: the order of the event really belongs to us.
    def did_fill_order(self, order_filled_event: OrderFilledEvent):
        order_id:str = order_filled_event.order_id
        exchange_trade_id:str = order_filled_event.exchange_trade_id
        
        # if order_id in self._maker_order_id_to_filled_trades.keys():
        #     self._maker_order_id_to_filled_trades[order_id].append(exchange_trade_id)
        #     self._maker_filled_trade_to_event[exchange_trade_id] = order_filled_event

        self._maker_filled_trades_set.add(exchange_trade_id)
        self._maker_filled_events_set.add(order_filled_event)

        #FIXME: need update inventory after taker!!

        return

    def process_order_fill_event(self, fill_event: OrderFilledEvent) -> None:
        base_asset, quote_asset = fill_event.trading_pair.split("-")
        quote_volume = fill_event.amount * fill_event.price
        base_volume = fill_event.amount

        for fee_asset, fee_amount in fill_event.trade_fee.flat_fees:
            if fill_event.trade_type == TradeType.BUY:
                if fee_asset == base_asset:
                    base_volume -= fee_amount
                elif fee_asset == quote_asset:
                    quote_volume += fee_amount
                else:
                    # Ok, some other asset used (like BNB), assume that we paid in base asset for simplicity
                    base_volume /= 1 + fill_event.trade_fee.percent
            else:
                if fee_asset == base_asset:
                    base_volume += fee_amount
                elif fee_asset == quote_asset:
                    # TODO: with new logic, this quote volume adjustment does not impacts anything
                    quote_volume -= fee_amount
                else:
                    # Ok, some other asset used (like BNB), assume that we paid in base asset for simplicity
                    base_volume /= 1 + fill_event.trade_fee.percent

        with self.sql_manager.get_new_session() as session:
            with session.begin():
                if fill_event.trade_type == TradeType.SELL:
                    record = InventoryCost.get_record(session, base_asset, quote_asset)
                    if not record:
                        raise RuntimeError("Sold asset without having inventory price set. This should not happen.")

                    # We're keeping initial buy price intact. Profits are not changing inventory price intentionally.
                    quote_volume = -(Decimal(record.quote_volume / record.base_volume) * base_volume)
                    base_volume = -base_volume

                InventoryCost.add_volume(
                    session, base_asset, quote_asset, base_volume, quote_volume
                )
