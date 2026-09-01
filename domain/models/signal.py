# -*- coding: utf-8 -*-
"""
domain/models/signal.py - V10 Domain Models (Signal, Decision, Action)
- Pure domain models with no external dependencies
- Immutable dataclasses with validation
- mypy --strict 준수 (Session 20): dict → Dict[str, Any], __post_init__ -> None 명시
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any
import time


class Action(str, Enum):
    """Trading action enum"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    ERROR = "ERROR"

    @classmethod
    def from_str(cls, value: str) -> "Action":
        upper = value.upper()
        for member in cls:
            if member.value == upper:
                return member
        return cls.HOLD

    @property
    def is_trade(self) -> bool:
        return self in (Action.BUY, Action.SELL)

    @property
    def label(self) -> str:
        return {
            "BUY": "Buy", "SELL": "Sell", "HOLD": "Hold", "ERROR": "Error"
        }.get(self.value, self.value)


@dataclass(frozen=True)
class Signal:
    """Strategy analysis result (immutable)"""
    ticker: str
    action: Action
    score: float
    confidence: float
    price: float
    entry_price: Optional[float] = None
    atr: float = 0.0
    positives: List[str] = field(default_factory=list)
    negatives: List[str] = field(default_factory=list)
    trace_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.ticker or len(self.ticker) != 6:
            raise ValueError(f"Invalid ticker: {self.ticker}")
        if not (0 <= self.score <= 1):
            raise ValueError(f"Invalid score: {self.score}")
        if not (0 <= self.confidence <= 1):
            raise ValueError(f"Invalid confidence: {self.confidence}")
        if self.action == Action.ERROR:
            return
        if self.price <= 0:
            raise ValueError(f"Invalid price: {self.price}")

    @classmethod
    def error(cls, ticker: str, message: str, trace_id: Optional[str] = None) -> "Signal":
        return cls(
            ticker=ticker, action=Action.ERROR, score=0.0, confidence=0.0,
            price=0.0, negatives=[message], trace_id=trace_id,
        )

    @property
    def is_trade(self) -> bool:
        return self.action.is_trade

    @property
    def action_label(self) -> str:
        return self.action.label

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker, "action": self.action.value,
            "action_label": self.action_label, "score": self.score,
            "confidence": self.confidence, "price": self.price,
            "entry_price": self.entry_price, "atr": self.atr,
            "positives": self.positives, "negatives": self.negatives,
            "trace_id": self.trace_id, "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class Decision:
    """Final decision (Signal + additional information)"""
    signal: Signal
    risk_adjusted_score: float
    recommended_quantity: int = 0
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    take_profit_3: float = 0.0
    max_hold_hours: float = 2.0

    def __post_init__(self) -> None:
        if not (0 <= self.risk_adjusted_score <= 1):
            raise ValueError(f"Invalid risk_adjusted_score: {self.risk_adjusted_score}")

    @property
    def ticker(self) -> str:
        return self.signal.ticker

    @property
    def action(self) -> Action:
        return self.signal.action

    @property
    def price(self) -> float:
        return self.signal.price

    @property
    def entry_price(self) -> Optional[float]:
        return self.signal.entry_price

    @property
    def atr(self) -> float:
        return self.signal.atr

    @property
    def confidence(self) -> float:
        return self.signal.confidence

    @property
    def is_trade(self) -> bool:
        return self.signal.is_trade

    @property
    def risk_reward_ratio(self) -> float:
        if self.stop_loss <= 0 or self.take_profit_1 <= 0:
            return 0.0
        if self.signal.action == Action.BUY:
            return (self.take_profit_1 - self.price) / (self.price - self.stop_loss)
        else:
            return (self.price - self.take_profit_1) / (self.stop_loss - self.price)

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.signal.to_dict(),
            "risk_adjusted_score": self.risk_adjusted_score,
            "recommended_quantity": self.recommended_quantity,
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "take_profit_3": self.take_profit_3,
            "max_hold_hours": self.max_hold_hours,
            "risk_reward_ratio": self.risk_reward_ratio,
        }
