"""
filters/macro_filter.py - v6.0 (Z-Score 가중 회귀 기반 매크로 필터)
- 8개 글로벌 지표를 Z-Score 정규화 후 동적 가중치 적용
- Risk-on/off 국면 반영
"""

import math

from core.logger import setup_logger
from scheduler.macro_collector import MacroData, get_cached_macro

logger = setup_logger("macro")


class MacroFilter:
    """Z-Score 기반 고급 매크로 필터"""

    # 각 지표의 이상적 방향 및 가중치 (Bullish 우호 기준)
    INDICATORS = {
        "kospi_trend": {"weight": 0.25, "optimal": "high", "neutral": 0.0},
        "spx_trend": {"weight": 0.20, "optimal": "high", "neutral": 0.0},
        "sox_trend": {"weight": 0.15, "optimal": "high", "neutral": 0.0},
        "usdkrw": {"weight": 0.10, "optimal": "low", "neutral": 1300.0},
        "vix": {"weight": 0.15, "optimal": "low", "neutral": 20.0},
        "bond_3y": {"weight": 0.05, "optimal": "low", "neutral": 4.0},
        "ktb_3y": {"weight": 0.05, "optimal": "low", "neutral": 3.5},
        "oil_price": {"weight": 0.05, "optimal": "low", "neutral": 75.0},
    }

    # 지표별 표준편차 (경험적 추정)
    STD_ESTIMATES = {
        "kospi_trend": 2.0,
        "spx_trend": 2.0,
        "sox_trend": 4.0,
        "usdkrw": 50.0,
        "vix": 8.0,
        "bond_3y": 0.8,
        "ktb_3y": 0.7,
        "oil_price": 15.0,
    }

    def __init__(self):
        self._macro: MacroData = get_cached_macro()

    def check(self, data: dict) -> dict:
        """매크로 점수 산출 (0~1)"""
        # 최신 거시 데이터 로드
        self._macro = get_cached_macro()

        weighted_score = 0.0
        total_weight = 0.0
        indicators = {}

        for key, spec in self.INDICATORS.items():
            # 현재값 가져오기 (data 우선, 없으면 macro)
            current = data.get(key, getattr(self._macro, key, spec["neutral"]))
            if current is None:
                current = spec["neutral"]

            # Z-Score 계산
            std = self.STD_ESTIMATES.get(key, 1.0)
            z_score = (current - spec["neutral"]) / std if std > 0 else 0.0

            # 방향 변환 (low가 좋은 지표는 -z_score)
            if spec["optimal"] == "low":
                z_score = -z_score

            # 0~1 스케일로 클램핑 (Sigmoid 변환)
            # z-score가 0이면 0.5, +2σ면 0.88, -2σ면 0.12
            sigmoid = 1.0 / (1.0 + math.exp(-z_score * 0.8))

            # 가중치 적용
            weight = spec["weight"]
            weighted_score += sigmoid * weight
            total_weight += weight
            indicators[key] = {"raw": current, "z_score": z_score, "score": sigmoid, "weight": weight}

        final_score = weighted_score / total_weight if total_weight > 0 else 0.5
        final_score = max(0.0, min(1.0, final_score))

        # 로그 (디버깅)
        logger.debug(
            f"📊 매크로 점수: {final_score:.3f} "
            f"(KOSPI: {indicators['kospi_trend']['score']:.2f}, "
            f"VIX: {indicators['vix']['score']:.2f}, "
            f"SPX: {indicators['spx_trend']['score']:.2f})"
        )

        return {
            "score": final_score,
            "indicators": indicators,
            "macro_data": self._macro.to_dict(),
        }
