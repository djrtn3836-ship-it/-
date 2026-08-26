# -*- coding: utf-8 -*-
"""
domain/strategies/base.py - V10 Strategy Abstract Base Class
- Defines interface that all strategies must implement
- No external dependencies (pure Python ABC)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class StrategyResult:
    """Strategy execution result"""
    name: str
    action: str  # 'BUY', 'SELL', 'HOLD'
    score: float  # 0~1
    confidence: float  # 0~1
    reasons: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_trade(self) -> bool:
        return self.action in ("BUY", "SELL")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "action": self.action,
            "score": self.score,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "metadata": self.metadata,
        }


class Strategy(ABC):
    """Strategy abstract base class"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name"""
        pass

    @property
    @abstractmethod
    def weight(self) -> float:
        """Strategy weight (0~1)"""
        pass

    @abstractmethod
    async def analyze(self, data: Dict[str, Any]) -> StrategyResult:
        """
        Execute strategy analysis

        Args:
            data: Market data (ticker, price, tech_data, regime, atr, etc.)

        Returns:
            StrategyResult: Analysis result
        """
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name}, weight={self.weight})>"