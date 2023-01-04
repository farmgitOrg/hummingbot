import asyncio
from decimal import Decimal
from typing import List

from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class SimpleAsyncPMM(ScriptStrategyBase):
    bid_spread = 0.08
    ask_spread = 0.08
    order_refresh_time = 15
    order_amount = 0.01
    create_timestamp = 0
    trading_pair = "ETH-USDT"
    exchange = "binance_paper_trade"
    # Here you can use for example the LastTrade price to use in your strategy
    price_source = PriceType.MidPrice
    on_going_task = False

    markets = {exchange: {trading_pair}}

    def on_tick(self):
        if self.create_timestamp <= self.current_timestamp and not self.on_going_task:
            self.on_going_task = True
            safe_ensure_future(self.async_task())

    async def async_task(self):
        cancels = await self.cancel_all_orders()
        if not all([result.success for result in cancels[0]]):
            self.logger().warning("There was an error when trying to cancel an order, retrying...")
            self.on_going_task = False
            return
        proposal: List[OrderCandidate] = self.create_proposal()
        proposal_adjusted: List[OrderCandidate] = self.adjust_proposal_to_budget(proposal)
        self.place_orders(proposal_adjusted)
        self.create_timestamp = self.order_refresh_time + self.current_timestamp
        self.on_going_task = False

    def create_proposal(self) -> List[OrderCandidate]:
        ref_price = self.connectors[self.exchange].get_price_by_type(self.trading_pair, self.price_source)
        buy_price = ref_price * Decimal(1 - self.bid_spread)
        sell_price = ref_price * Decimal(1 + self.ask_spread)

        buy_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                   order_side=TradeType.BUY, amount=Decimal(self.order_amount), price=buy_price)

        sell_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                    order_side=TradeType.SELL, amount=Decimal(self.order_amount), price=sell_price)

        return [buy_order, sell_order]

    def adjust_proposal_to_budget(self, proposal: List[OrderCandidate]) -> List[OrderCandidate]:
        proposal_adjusted = self.connectors[self.exchange].budget_checker.adjust_candidates(proposal, all_or_none=True)
        return proposal_adjusted

    def place_orders(self, proposal: List[OrderCandidate]) -> None:
        for order in proposal:
            self.place_order(connector_name=self.exchange, order=order)

    def place_order(self, connector_name: str, order: OrderCandidate):
        if order.order_side == TradeType.SELL:
            self.sell(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                      order_type=order.order_type, price=order.price)
        elif order.order_side == TradeType.BUY:
            self.buy(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                     order_type=order.order_type, price=order.price)

    async def cancel_all_orders(self):
        tasks = [exchange.cancel_all(timeout_seconds=5) for exchange in self.connectors.values()]
        cancels = await asyncio.gather(*tasks)
        return cancels