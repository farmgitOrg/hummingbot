#!/usr/bin/env python

from sqlalchemy import BigInteger, Column, Index, Integer, Text

from . import HummingbotBase


class StatBalance(HummingbotBase):
    __tablename__ = "StatBalance"
    __table_args__ = (Index("o_timestamp_index",
                            "ts"),)

    id = Column(Integer, primary_key=True, nullable=False)
    ts = Column(BigInteger, nullable=False)
    exchange = Column(Text, nullable=False)
    symbol = Column(Text, nullable=False)
    balance = Column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"StatBalance(id='{self.id}', ts='{self.ts}', symbol='{self.symbol}', " \
            f"balance={self.balance}), exchange={self.exchange}"
