import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.model.inventory_cost import InventoryCost
from hummingbot.model.sql_connection_manager import SQLConnectionManager
from hummingbot.strategy.maker_taker_market_pair import MakerTakerMarketPair

s_decimal_0 = Decimal("0")

class TakerDelegate:
    @classmethod
    def logger(cls):
        global pmm_taker_delegate_logger
        if pmm_taker_delegate_logger is None:
            pmm_taker_delegate_logger = logging.getLogger(__name__)
        return pmm_taker_delegate_logger
    
    # def __init__(self, strategy: PureMarketMakingStrategy, market_pairs: MakerTakerMarketPair) -> None:
    def __init__(self, market_pairs: MakerTakerMarketPair) -> None:
        self._maker_market = market_pairs.maker.market
        self._taker_market = market_pairs.taker.market
        self._market_pairs = market_pairs
        # self._strategy = strategy

    def debug(self):
        buy_price = self._taker_market.get_price(self._market_pairs.taker.trading_pair, True)
        sell_price = self._taker_market.get_price(self._market_pairs.taker.trading_pair, False)
        self.logger().warning(f"##@@## buy/sell price is : {buy_price} / {sell_price}, diff: {sell_price - buy_price}")
        
    def get_price(self, is_buy:bool) -> Optional[Decimal]:
        with self.sql_manager.get_new_session() as session:
            with session.begin():
                record = InventoryCost.get_record(
                    session, self.base_asset, self.quote_asset
                )

                if record is None or record.base_volume is None or record.base_volume is None:
                    return None

                try:
                    price = record.quote_volume / record.base_volume
                except InvalidOperation:
                    return None
                return Decimal(price)

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
