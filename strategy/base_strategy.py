"""
strategy/base_strategy.py - v1.2 FINAL (config 기반 가중치 로드)
- 모든 전략의 추상 기본 클래스
- _safe_get() 메서드 추가 (딕셔너리에서 None/오류 방지)
- config에서 기본 가중치를 읽어올 수 있도록 get_default_weight() 메서드 제공
"""

from abc import ABC, abstractmethod
from typing import Dict, Any

from core.config import get_config

config = get_config()


class BaseStrategy(ABC):
    """전략 추상 기본 클래스 (v1.2)"""

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

    def _safe_get(self, data: Dict, key: str, default: Any = 0.0) -> Any:
        """안전한 값 추출 (None 또는 KeyError 방지)"""
        value = data.get(key, default)
        return default if value is None else value

    def get_default_weight(self, config_key: str, fallback: float) -> float:
        """config에서 기본 가중치를 읽어옴"""
        return config.get_float(config_key, fallback)