from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, String

from .base import Base


class PositionState(Base):  # pylint: disable=too-few-public-methods
    """
    持久化仓位状态 - 用于 ATR trailing 策略
    """
    __tablename__ = "position_states"

    symbol = Column(String, primary_key=True)
    entry_price = Column(Float, nullable=False)
    entry_time = Column(DateTime, nullable=False)
    highest_price = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=False)
    trail_active = Column(Boolean, nullable=False, default=False)
    atr = Column(Float, nullable=False)
    atr_pct = Column(Float, nullable=False)
    last_atr_update_time = Column(DateTime, nullable=False)

    def __init__(
        self,
        symbol: str,
        entry_price: float,
        entry_time: datetime,
        highest_price: float,
        stop_price: float,
        trail_active: bool,
        atr: float,
        atr_pct: float,
        last_atr_update_time: datetime,
    ):
        self.symbol = symbol
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.highest_price = highest_price
        self.stop_price = stop_price
        self.trail_active = trail_active
        self.atr = atr
        self.atr_pct = atr_pct
        self.last_atr_update_time = last_atr_update_time

    def info(self):
        return {
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time.isoformat(),
            "highest_price": self.highest_price,
            "stop_price": self.stop_price,
            "trail_active": self.trail_active,
            "atr": self.atr,
            "atr_pct": self.atr_pct,
            "last_atr_update_time": self.last_atr_update_time.isoformat(),
        }
