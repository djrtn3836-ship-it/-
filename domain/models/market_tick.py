# -*- coding: utf-8 -*-
"""
domain/models/market_tick.py - V10 Pure Domain Model
- Converts WebSocket raw data to validated object
- No external dependencies (pure Python dataclass)
- Immutable object design
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class MarketTick:
    """
    Real-time tick data (immutable)

    Attributes:
        ticker: 6-digit stock code
        price: Current price (positive)
        volume: Trading volume (positive)
        timestamp: Unix timestamp of receipt
        trace_id: Trace ID for butterfly effect analysis
        raw: Raw data for debugging (optional)
    """
    ticker: str
    price: float
    volume: int
    timestamp: float
    trace_id: Optional[str] = None
    raw: Optional[dict] = None

    def __post_init__(self):
        """Validate fields after initialization"""
        if not self.ticker or len(self.ticker) != 6 or not self.ticker.isdigit():
            raise ValueError(f"Invalid ticker: {self.ticker}")
        if self.price <= 0:
            raise ValueError(f"Invalid price: {self.price}")
        if self.volume < 0:
            raise ValueError(f"Invalid volume: {self.volume}")

    @classmethod
    def from_raw(cls, data: dict, trace_id: Optional[str] = None) -> "MarketTick":
        """
        Create MarketTick from raw WebSocket data.
        Extracts price from key "10" and volume from key "13" in values dict.
        """
        values = data.get("values", {})
        raw_price = values.get("10")
        raw_volume = values.get("13", 0)

        if raw_price is None:
            raise ValueError("Missing price in raw data")

        try:
            price = abs(float(str(raw_price).replace(",", "")))
        except (ValueError, TypeError):
            raise ValueError(f"Invalid price format: {raw_price}")

        try:
            volume = int(str(raw_volume).replace(",", ""))
        except (ValueError, TypeError):
            volume = 0

        return cls(
            ticker=data.get("ticker", ""),
            price=price,
            volume=volume,
            timestamp=datetime.now().timestamp(),
            trace_id=trace_id,
            raw=data if trace_id else None,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary (serialization)"""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "volume": self.volume,
            "timestamp": self.timestamp,
            "trace_id": self.trace_id,
        }

    def change_rate(self, baseline: float) -> float:
        """Calculate change rate from baseline"""
        if baseline <= 0:
            return 0.0
        return (self.price - baseline) / baseline