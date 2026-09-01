# -*- coding: utf-8 -*-
"""
domain/strategies/base.py - V10 Strategy Abstract Base Class
- mypy --strict 준수 (Session 20): to_dict() dict → Dict[str, Any]
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class StrategyResult:
    """Strategy execution result"""
    name: str
    action: str
    score: float
    confidence: float
    reasons: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_trade(self) -> bool:
        return self.action in ("BUY", "SELL")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "action": self.action, "score": self.score,
            "confidence": self.confidence, "reasons": self.reasons,
            "metadata": self.metadata,
        }


class Strategy(ABC):
    """Strategy abstract base class.

    weight는 읍기 전용 프로퍼티(setter 없음)입니다 — 실제 소스로 확인 완료
    (Session 15/20). 전략 가중치를 튜닝하려면 전략 객체를 직접 수정하지 말고
    application/analysis/signal_pipeline.py의 _strategy_weights 딕셔너리를
    사용하세요.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def weight(self) -> float:
        pass

    @abstractmethod
    async def analyze(self, data: Dict[str, Any]) -> StrategyResult:
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name}, weight={self.weight})>"
