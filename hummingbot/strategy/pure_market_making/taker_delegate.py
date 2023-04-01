import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from hummingbot.core.data_type.common import TradeType, OrderType
from hummingbot.model.inventory_cost import InventoryCost
from hummingbot.model.sql_connection_manager import SQLConnectionManager
from hummingbot.strategy.maker_taker_market_pair import MakerTakerMarketPair
from hummingbot.strategy.strategy_base import StrategyBase

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

s_float_nan = float("nan")
s_decimal_zero = Decimal(0)
s_decimal_nan = Decimal("nan")
pmm_taker_delegate_logger = None

class TakerDelegate:
    @classmethod
    def logger(cls):
        global pmm_taker_delegate_logger
        if pmm_taker_delegate_logger is None:
            pmm_taker_delegate_logger = logging.getLogger(__name__)
        return pmm_taker_delegate_logger

    def log_with_clock(self, log_level: int, msg: str, **kwargs):
        # clock_timestamp = pd.Timestamp(self._current_timestamp, unit="s", tz="UTC")
        # self.logger().log(log_level, f"{msg} [clock={str(clock_timestamp)}]", **kwargs)
        self.logger().log(log_level, f"{msg} ", **kwargs)

    # def __init__(self, strategy: PureMarketMakingStrategy, market_pairs: MakerTakerMarketPair) -> None:
    def __init__(self,
                 strategy: StrategyBase,
                 market_pairs: MakerTakerMarketPair, 
                 force_hedge_initerval:float,
                 hedge_amount_threshold:Decimal,
                 slippage_buffer: Decimal
                 ) -> None:
        self._strategy = strategy
        self._maker_market = market_pairs.maker.market
        self._taker_market = market_pairs.taker.market
        self._market_pairs = market_pairs
        self._slippage_buffer = slippage_buffer / Decimal("100")
        self._force_hedge_initerval = force_hedge_initerval
        self._hedge_amount_threshold = hedge_amount_threshold
        self._taker_order_id_to_maker_filled_trades = {}  # taker order id -> 所对冲的maker filled trades events
        self._taker_order_id_to_maker_filled_amount_unhedged = {} # taker order id -> hedge amount,  >0 buy, <0 sell

        self._hedge_failed_amount: Decimal = Decimal(0) # overall hedge failed amount, >0 buy, <0 fail, on maker's view

        self._maker_filled_trade_set:set = set() # used to store filled trade id
        self._maker_filled_trade_to_event_map = {}  # filled trade id -> filled event

    def debug(self):
        # buy_price = self._taker_market.get_price(self._market_pairs.taker.trading_pair, True)
        # sell_price = self._taker_market.get_price(self._market_pairs.taker.trading_pair, False)
        buy_price = self._market_pairs.taker.get_price(True)
        sell_price = self._market_pairs.taker.get_price(False)
        self.logger().warning(f"##@@## buy/sell price is : {buy_price} / {sell_price}, diff: {sell_price - buy_price}")
        
    def get_taker_price(self, base_size:Decimal, is_buy:bool) -> Decimal:
        # taker_price = self._taker_market.get_price_for_volume(self._market_pairs.taker.trading_pair,
        #                                                     is_buy, base_size).result_price
        taker_price = self._market_pairs.taker.get_price_for_volume(is_buy, base_size).result_price
        return taker_price

    def need_do_hedge(self) -> bool:
        return True

    def check_and_process_hedge(self, hedge_tick_reached:bool):
        maker_buy_filled_amount = Decimal(0)
        maker_sell_filled_amount = Decimal(0)
        maker_buy_filled_volume = Decimal(0)
        maker_sell_filled_volume = Decimal(0)
        
        for tradeid in self._maker_filled_trade_set:
            event = self._maker_filled_trade_to_event_map.get(tradeid)
            if event is None:
                continue
            if event.trade_type is TradeType.BUY:
                maker_buy_filled_amount += event.amount
                maker_buy_filled_volume += event.amount * event.price
                self.logger().info(f"check_and_process_hedge: unhedged maker buy tradeid: {tradeid}, amount {event.amount}, accumulated {maker_buy_filled_amount}")
            else:
                maker_sell_filled_amount += event.amount
                maker_sell_filled_volume += event.amount * event.price
                self.logger().info(f"check_and_process_hedge: unhedged maker sell tradeid: {tradeid}, amount {event.amount}, accumulated {maker_sell_filled_amount}")

        if maker_buy_filled_amount > 0:
            self.log_with_clock(
                logging.WARN,
                f"check_and_process_hedge: maker_buy_filled_amount: {maker_buy_filled_amount} @ avgprice {maker_buy_filled_volume/maker_buy_filled_amount} "
            )
        if maker_sell_filled_amount > 0:
            self.log_with_clock(
                logging.WARN,
                f"check_and_process_hedge: maker_sell_filled_amount: {maker_sell_filled_amount} @ avgprice {maker_sell_filled_volume/maker_sell_filled_amount}"
            )

        maker_unbalanced_amount = maker_buy_filled_amount - maker_sell_filled_amount
        maker_unbalanced_amount += self._hedge_failed_amount # add previous failed order
        self.logger().info(f"check_and_process_hedge: maker_unbalanced_amount: {maker_unbalanced_amount} ( with _hedge_failed_amount: {self._hedge_failed_amount} ), "
                           f"hedge_tick_reached: {hedge_tick_reached}")
        
        # TODO: [0, hedge threshold)
        if abs(maker_unbalanced_amount) < self._hedge_amount_threshold/10 or (abs(maker_unbalanced_amount) < self._hedge_amount_threshold and hedge_tick_reached is False):
            self.logger().debug(f"check_and_process_hedge: maker_unbalanced_amount: {maker_unbalanced_amount}, hedge_tick_reached: {hedge_tick_reached}, skip hedging")
            return

        #update the event record beforehand, to avoid any blocking ops later
        trade_set_onhedging = self._maker_filled_trade_set.copy() # FIXME: handle trade_set_onhedging not empty case
        self._maker_filled_trade_set = set()

        order_type = self._market_pairs.taker.market.get_taker_order_type() #TODO: user MARKET order
        taker_trading_pair = self._market_pairs.taker.trading_pair
        taker_market = self._market_pairs.taker.market
        base_rate = 1
        expiration_seconds = 100 # FIXME:
        quantized_hedge_amount:Decimal = 0
        order_id = None
        # reach this point means:  abs( maker_unbalanced_amount ) > _hedge_amount_threshold, or hedge_tick_reached is true and maker_unbalanced_amount != 0
        # buy amount > sell amount on maker, sell on taker market
        # if maker_unbalanced_amount >= self._hedge_amount_threshold or (maker_unbalanced_amount> 0 and hedge_tick_reached):
        if maker_unbalanced_amount > 0:
            amount = maker_unbalanced_amount

            taker_slippage_adjustment_factor = Decimal("1") - self._slippage_buffer
            sell_price = self.get_taker_price(amount, False)
            
            sell_price *= taker_slippage_adjustment_factor # taker 侧 滑点调整
            sell_price = taker_market.quantize_order_price(taker_trading_pair, sell_price) # 重新量化 报价, 按照报价单位向下取整
            # self.log_with_clock(logging.INFO, f"Slippage buffer adjusted order_price: {sell_price}")
            
            hedged_order_quantity = min(
                amount / base_rate,
                taker_market.get_available_balance(self._market_pairs.taker.base_asset)
            )
            quantized_hedge_amount = taker_market.quantize_order_amount(taker_trading_pair, Decimal(hedged_order_quantity)) #量化到taker market的下单整数倍

            self.logger().warn(f"##@@ sell_price={sell_price}")
            self.logger().warn(f"##@@ amount/base_rate = {amount/base_rate}, t2={taker_market.get_available_balance(self._market_pairs.taker.base_asset)}")
            self.logger().warn(f"##@@ hedged_order_quantity = {hedged_order_quantity}, quantized_hedge_amount = {quantized_hedge_amount}")

            # if order_type is OrderType.MARKET:
            #     sell_price = s_decimal_nan
            self.log_with_clock(
                logging.WARN,
                f"check_and_process_hedge: taker SELL {quantized_hedge_amount} @ {sell_price}"
            )
            
            if quantized_hedge_amount > s_decimal_zero:
                try:
                    order_id = self._strategy.sell_with_specific_market(self._market_pairs.taker, quantized_hedge_amount,
                                                            order_type=order_type, price=sell_price,
                                                            expiration_seconds=expiration_seconds) # TODO:
                except ValueError as e:
                    self.logger().error(f"taker_delegate: Placing a taker SELL order "
                                        f"failed with the following error: {str(e)}")
            else:
                self.logger().error(f"Current taker SELL amount {hedged_order_quantity} "
                    f"is less than the minimum order amount allowed on the taker market. No hedging possible yet."
                )
        # buy amount < sell amount on maker, buy on taker market
        else:
            amount = -maker_unbalanced_amount
            
            taker_slippage_adjustment_factor = Decimal("1") + self._slippage_buffer
            buy_price = self.get_taker_price(amount, True)


            buy_price *= taker_slippage_adjustment_factor
            buy_price = taker_market.quantize_order_price(taker_trading_pair, buy_price)
            # self.log_with_clock(logging.INFO, f"Slippage buffer adjusted order_price: {buy_price}")
            
            hedged_order_quantity = min(
                amount / base_rate,
                taker_market.get_available_balance(self._market_pairs.taker.quote_asset) / buy_price
            )
            quantized_hedge_amount = taker_market.quantize_order_amount(taker_trading_pair, Decimal(hedged_order_quantity)) #量化到taker market的下单整数倍

            self.logger().warn(f"##@@ buy_price={buy_price}")
            self.logger().warn(f"##@@ amount/base_rate = {amount/base_rate}, t2={taker_market.get_available_balance(self._market_pairs.taker.quote_asset)/buy_price}")
            self.logger().warn(f"##@@ hedged_order_quantity = {hedged_order_quantity}, quantized_hedge_amount = {quantized_hedge_amount}")

            # if order_type is OrderType.MARKET:
            #     buy_price = s_decimal_nan
            self.log_with_clock(
                logging.WARN,
                f"check_and_process_hedge: taker BUY {quantized_hedge_amount} @ {buy_price}"
            )
            
            if quantized_hedge_amount > s_decimal_zero:
                try:
                    order_id = self._strategy.buy_with_specific_market(self._market_pairs.taker, quantized_hedge_amount,
                                                            order_type=order_type, price=buy_price,
                                                            expiration_seconds=expiration_seconds)
                except ValueError as e:
                    self.logger().error(f"taker_delegate: Placing a taker BUY order "
                                        f"failed with the following error: {str(e)}")
            else:
                self.logger().error(f"Current taker BUY amount {hedged_order_quantity} "
                    f"is less than the minimum order amount allowed on the taker market. No hedging possible yet."
                )
        # else:
        #     self.log_with_clock(
        #         logging.WARN,
        #         f"taker_delegate: maker_unbalanced_amount {maker_unbalanced_amount}, " 
        #         f"threshold {self._hedge_amount_threshold}, hedge_tick_reached {hedge_tick_reached}, skip hedging"
        #     )

        #FIXME: handle failed order remain amount
        if order_id is None:
            # recover event record if any error
            # self._hedge_failed_amount keep unchanged.
            self._maker_filled_trade_set = self._maker_filled_trade_set | trade_set_onhedging # new maker filled may happend during above hedging
            self.logger().error(f"create hedging taker order failed")
            return

        self.logger().warn(f"check_and_process_hedge: create taker order: {order_id}")

        self._hedge_failed_amount = 0 # previous hedge failed amount will be accounted into current taker order, so reset here
        self._taker_order_id_to_maker_filled_amount_unhedged[order_id] = maker_unbalanced_amount # TODO: quantized_hedge_amount
        self._taker_order_id_to_maker_filled_trades[order_id] = trade_set_onhedging
        
        return order_id

    def did_create_buy_order(self, order_created_event: BuyOrderCreatedEvent):
        return
    def did_create_sell_order(self, order_created_event: SellOrderCreatedEvent):
        return
    
    def _process_uncompleted_taker_order(self, order_id:str):
        if order_id in self._taker_order_id_to_maker_filled_trades.keys():
            self.log_with_clock(
                logging.ERROR,
                f"taker_delegate: _process_uncompleted_taker_order {order_id}"
            )
            tradeidset = self._taker_order_id_to_maker_filled_trades.get(order_id) # reset  maker filled event
            for tradeid in tradeidset:
                del self._maker_filled_trade_to_event_map[tradeid]
            del self._taker_order_id_to_maker_filled_trades[order_id]
            
            self._hedge_failed_amount += self._taker_order_id_to_maker_filled_amount_unhedged[order_id] # reocrd hedge failed amount
            del self._taker_order_id_to_maker_filled_amount_unhedged[order_id]
            self._strategy.notify_hb_app_with_timestamp(f"Taker order {order_id} failed!")

    def did_cancel_order(self, order_canceled_event: OrderCancelledEvent):
        order_id:str = order_canceled_event.order_id
        if order_id in self._taker_order_id_to_maker_filled_trades.keys():
            self.log_with_clock(
                logging.ERROR,
                f"taker_delegate: did_cancel_order {order_id}"
            )
            self._process_uncompleted_taker_order(order_id)
            
    def did_fail_order(self, order_failed_event: MarketOrderFailureEvent):
        order_id:str = order_failed_event.order_id
        if order_id in self._taker_order_id_to_maker_filled_trades.keys():
            self.log_with_clock(
                logging.ERROR,
                f"taker_delegate: did_fail_order {order_id}"
            )
            self._process_uncompleted_taker_order(order_id)

    def did_expire_order(self, order_expired_event: OrderExpiredEvent):   #FIXME: for expired order, do we need manually cancel it?
        order_id:str = order_expired_event.order_id
        if order_id in self._taker_order_id_to_maker_filled_trades.keys():
            self.log_with_clock(
                logging.ERROR,
                f"taker_delegate: did_expire_order {order_id}"
            )
            self._process_uncompleted_taker_order(order_id)

    def _process_completed_taker_order(self, order_id:str):
        if order_id in self._taker_order_id_to_maker_filled_trades.keys():
            self.log_with_clock(
                logging.ERROR,
                f"taker_delegate: _process_completed_taker_order {order_id}"
            )
            tradeidset = self._taker_order_id_to_maker_filled_trades.get(order_id)  # reset  maker filled event
            for tradeid in tradeidset:
                del self._maker_filled_trade_to_event_map[tradeid]
            del self._taker_order_id_to_maker_filled_trades[order_id]
            del self._taker_order_id_to_maker_filled_amount_unhedged[order_id]

    def did_complete_buy_order(self, order_completed_event: BuyOrderCompletedEvent):
        order_id:str = order_completed_event.order_id
        if order_id in self._taker_order_id_to_maker_filled_trades.keys():
            self.log_with_clock(
                logging.ERROR,
                f"taker_delegate: did_complete_buy_order {order_id}"
            )
            self._process_completed_taker_order(order_id)
            self._strategy.notify_hb_app_with_timestamp(
                f"Taker BUY order {order_completed_event.base_asset_amount} {order_completed_event.base_asset} "
                f"@ {order_completed_event.quote_asset_amount} {order_completed_event.quote_asset} is filled"
            )

    def did_complete_sell_order(self, order_completed_event: SellOrderCompletedEvent):
        order_id:str = order_completed_event.order_id
        if order_id in self._taker_order_id_to_maker_filled_trades.keys():
            self.log_with_clock(
                logging.ERROR,
                f"taker_delegate: did_complete_sell_order {order_id}"
            )
            self._process_completed_taker_order(order_id)
            self._strategy.notify_hb_app_with_timestamp(
                f"Taker SELL order {order_completed_event.base_asset_amount} {order_completed_event.base_asset} "
                f"@ {order_completed_event.quote_asset_amount} {order_completed_event.quote_asset} is filled"
            )

    #prerequisite: the order of the event really belongs to us.
    def did_fill_maker_order(self, order_filled_event: OrderFilledEvent):
        order_id:str = order_filled_event.order_id
        exchange_trade_id:str = order_filled_event.exchange_trade_id
        
        self._maker_filled_trade_set.add(exchange_trade_id)
        self._maker_filled_trade_to_event_map[exchange_trade_id] = order_filled_event
        #FIXME: need update inventory after taker!!

        return

    def did_fill_taker_order(self, order_filled_event: OrderFilledEvent):
        order_id:str = order_filled_event.order_id
        exchange_trade_id:str = order_filled_event.exchange_trade_id
        
        if order_id in self._taker_order_id_to_maker_filled_trades.keys():
            if order_filled_event.trade_type is TradeType.BUY: # taker buy filled
                self._taker_order_id_to_maker_filled_amount_unhedged[order_id] += order_filled_event.amount
            else:
                self._taker_order_id_to_maker_filled_amount_unhedged[order_id] -= order_filled_event.amount
    
        return
