# filters/macro_filter.py - v6.0.1 (mypy strict 적용 - Session 24)
import math
from typing import Any, Dict

from core.logger import setup_logger
from scheduler.macro_collector import MacroData, get_cached_macro

logger = setup_logger("macro")


class MacroFilter:
    """Z-Score 기반 고급 매크로 필터"""

    INDICATORS: Dict[str, Dict[str, Any]] = {
        "kospi_trend": {"weight": 0.25, "optimal": "high", "neutral": 0.0},
        "spx_trend":   {"weight": 0.20, "optimal": "high", "neutral": 0.0},
        "sox_trend":   {"weight": 0.15, "optimal": "high", "neutral": 0.0},
        "usdkrw":      {"weight": 0.10, "optimal": "low",  "neutral": 1300.0},
        "vix":         {"weight": 0.15, "optimal": "low",  "neutral": 20.0},
        "bond_3y":     {"weight": 0.05, "optimal": "low",  "neutral": 4.0},
        "ktb_3y":      {"weight": 0.05, "optimal": "low",  "neutral": 3.5},
        "oil_price":   {"weight": 0.05, "optimal": "low",  "neutral": 75.0},
    }

    STD_ESTIMATES: Dict[str, float] = {
        "kospi_trend": 2.0, "spx_trend": 2.0, "sox_trend": 4.0, "usdkrw": 50.0,
        "vix": 8.0, "bond_3y": 0.8, "ktb_3y": 0.7, "oil_price": 15.0,
    }

    def __init__(self) -> None:
        self._macro: MacroData = get_cached_macro()

    def check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """매크로 점수 산출 (0~1)"""
        self._macro = get_cached_macro()

        weighted_score: float = 0.0
        total_weight: float = 0.0
        indicators: Dict[str, Dict[str, Any]] = {}

        for key, spec in self.INDICATORS.items():
            # 원본 로직 보존: data에 키가 없으면 macro값, macro도 없으면 neutral
            current: Any = data.get(key, getattr(self._macro, key, spec["neutral"]))
            if current is None:
                current = spec["neutral"]

            std: float = self.STD_ESTIMATES.get(key, 1.0)
            z_score: float = (float(current) - float(spec["neutral"])) / std if std > 0 else 0.0
            if spec["optimal"] == "low":
                z_score = -z_score

            sigmoid: float = 1.0 / (1.0 + math.exp(-z_score * 0.8))
            weight: float = float(spec["weight"])
            weighted_score += sigmoid * weight
            total_weight += weight
            indicators[key] = {"raw": current, "z_score": z_score, "score": sigmoid, "weight": weight}

        final_score: float = weighted_score / total_weight if total_weight > 0 else 0.5
        final_score = max(0.0, min(1.0, final_score))

        logger.debug(
            "📊 매크로 점수: %.3f (KOSPI: %.2f, VIX: %.2f, SPX: %.2f)",
            final_score, indicators["kospi_trend"]["score"],
            indicators["vix"]["score"], indicators["spx_trend"]["score"],
        )
        return {"score": final_score, "indicators": indicators, "macro_data": self._macro.to_dict()}
