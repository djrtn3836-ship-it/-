"""
strategy/base_strategy.py - v1.0 FINAL (전략 추상 클래스)
- 모든 전략은 이 클래스를 상속받아 analyze() 구현
- 각 전략은 독립적으로 점수(0~1)와 액션(BUY/SELL/HOLD)을 반환
"""

from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseStrategy(ABC):
    """전략 추상 기본 클래스"""

    @abstractmethod
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        전략 분석 실행
        Args:
            data: 종목 데이터 (price, ohlcv, 기술지표 등)
        Returns:
            {
                'score': float,       # 0~1 (매수 강도)
                'action': str,        # 'BUY', 'SELL', 'HOLD'
                'confidence': float,  # 0~1
                'reason': str,        # 판단 근거
                'details': dict       # 추가 정보
            }
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """전략 이름"""
        pass

    @property
    @abstractmethod
    def weight(self) -> float:
        """전략 가중치 (0~1) - 설정 파일에서 로드 가능"""
        pass