from typing import Any, Dict

from sqlalchemy import Column, Index, Integer, Text

from hummingbot.model import HummingbotBase


class OrderHedging(HummingbotBase):
    __tablename__ = "OrderHedging"
    __table_args__ = (Index("o_maker_trade_index",
                            "maker_trade_id"),
                      Index("o_hedging_trade_index",
                            "hedging_trade_id")
                      )

    id = Column(Integer, primary_key=True, nullable=False)
    maker_trade_id = Column(Text, nullable=False)
    hedging_trade_id = Column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"OrderHedging(id={self.id}, " \
               f"maker_trade_id={self.maker_trade_id}, hedging_trade_id={self.hedging_trade_id}"

    @staticmethod
    def to_bounty_api_json(order_detail: "OrderHedging") -> Dict[str, Any]:
        return {
            "maker_trade_id": order_detail.maker_trade_id,
            "hedging_trade_id": order_detail.hedging_trade_id,
            "raw_json": {
            }
        }
