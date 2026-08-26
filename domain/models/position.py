# -*- coding: utf-8 -*-
"""
domain/models/position.py - V10 Pure Domain Models (Position, TrailingStopState)
- Position and trailing stop state models
- No external dependencies (pure Python dataclass + Enum)
- PnL calculation, return rate calculation included
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum



class PositionSide(str, Enum):
    """Position direction"""
    LONG = "LONG"   # Long position (buy)
    SHORT = "SHORT" # Short position (sell)

    @property
    def is_long(self) -> bool:
        return self == PositionSide.LONG

    @property
    def is_short(self) -> bool:
        return self == PositionSide.SHORT


@dataclass
class Position:
    """
    Position information (mutable - updated with market data)

    Attributes:
        ticker: Stock code
        side: Direction (LONG/SHORT)
        entry_price: Entry price
        quantity: Quantity held
        current_price: Current price
        entry_time: Entry time
        pnl: Profit/Loss in KRW
        pnl_pct: Profit/Loss percentage (%)
    """
    ticker: str
    side: PositionSide
    entry_price: float
    quantity: int
    current_price: float
    entry_time: datetime = field(default_factory=datetime.now)
    pnl: float = 0.0
    pnl_pct: float = 0.0

    def __post_init__(self):
        """Initial PnL calculation"""
        self.update_price(self.current_price)

    def update_price(self, new_price: float) -> None:
        """Update current price and recalculate PnL"""
        if new_price <= 0:
            return
        self.current_price = new_price
        if self.side.is_long:
            self.pnl = (self.current_price - self.entry_price) * self.quantity
        else:
            self.pnl = (self.entry_price - self.current_price) * self.quantity
        self.pnl_pct = (self.pnl / (self.entry_price * self.quantity)) * 100 if self.entry_price > 0 else 0.0

    @property
    def position_value(self) -> float:
        """Current position value"""
        return self.current_price * self.quantity

    @property
    def entry_value(self) -> float:
        """Entry position value"""
        return self.entry_price * self.quantity

    @property
    def is_profit(self) -> bool:
        """Whether position is in profit"""
        return self.pnl > 0

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "side": self.side.value,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "current_price": self.current_price,
            "entry_time": self.entry_time.isoformat(),
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
        }


@dataclass
class TrailingStopState:
    """
    Trailing stop state machine state object

    Attributes:
        position: Reference position
        highest_price: Highest price (LONG) / lowest_price: Lowest price (SHORT)
        current_stop: Current stop loss price
        tp1_price, tp2_price, tp3_price: Take profit prices
        tp_hit_level: TP level reached (0=none, 1=TP1, 2=TP2, 3=TP3)
        remaining_qty: Remaining quantity ratio (1.0 -> 0.5 -> 0.2 -> 0.0)
        atr: Current ATR value
        last_advice_time: Last consensus advice time
    """
    position: Position
    highest_price: float
    lowest_price: float
    current_stop: float
    tp1_price: float
    tp2_price: float
    tp3_price: float
    atr: float = 0.0
    tp_hit_level: int = 0
    remaining_qty: float = 1.0
    last_advice_time: float = 0.0
    last_update_time: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        self.last_update_time = datetime.now().isoformat()

    def update_prices(self, current_price: float, atr: float = 0.0) -> None:
        """Update current price and ATR"""
        self.position.update_price(current_price)
        if atr > 0:
            self.atr = atr

        # Update high/low
        if self.position.side.is_long and current_price > self.highest_price:
            self.highest_price = current_price
        elif self.position.side.is_short and current_price < self.lowest_price:
            self.lowest_price = current_price

        self.last_update_time = datetime.now().isoformat()

    @property
    def is_active(self) -> bool:
        """Whether position is still active (not fully exited)"""
        return self.remaining_qty > 0 and self.position.quantity > 0

    @property
    def stop_distance_pct(self) -> float:
        """Distance from current price to stop loss (%)"""
        if self.current_stop <= 0 or self.position.current_price <= 0:
            return 100.0
        if self.position.side.is_long:
            return (self.position.current_price - self.current_stop) / self.position.current_price * 100
        else:
            return (self.current_stop - self.position.current_price) / self.position.current_price * 100

    def to_dict(self) -> dict:
        return {
            "ticker": self.position.ticker,
            "side": self.position.side.value,
            "entry_price": self.position.entry_price,
            "current_price": self.position.current_price,
            "current_stop": self.current_stop,
            "tp1": self.tp1_price,
            "tp2": self.tp2_price,
            "tp3": self.tp3_price,
            "tp_hit_level": self.tp_hit_level,
            "remaining_qty": self.remaining_qty,
            "atr": self.atr,
            "pnl_pct": self.position.pnl_pct,
            "stop_distance_pct": self.stop_distance_pct,
            "last_update": self.last_update_time,
        }