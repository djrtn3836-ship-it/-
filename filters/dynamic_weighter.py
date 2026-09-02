# filters/dynamic_weighter.py - v5.1.3 (mypy strict 적용 - Session 24)
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class DynamicWeighter:
    """동적 가중치 계산기 v5.1.3"""

    REGIME_WEIGHTS: Dict[str, Dict[str, Any]] = {
        "Bull":      {"trend": 0.30, "risk": 0.25, "flow": {"foreigner": 0.20, "institution": 0.12, "program": 0.08, "retail": 0.05}},
        "Sideways":  {"trend": 0.30, "risk": 0.30, "flow": {"foreigner": 0.15, "institution": 0.12, "program": 0.08, "retail": 0.05}},
        "Bear":      {"trend": 0.25, "risk": 0.40, "flow": {"foreigner": 0.12, "institution": 0.15, "program": 0.05, "retail": 0.03}},
        "Panic":     {"trend": 0.15, "risk": 0.50, "flow": {"foreigner": 0.10, "institution": 0.15, "program": 0.05, "retail": 0.05}},
        "Recovery":  {"trend": 0.30, "risk": 0.25, "flow": {"foreigner": 0.20, "institution": 0.12, "program": 0.08, "retail": 0.05}},
    }

    def __init__(self) -> None:
        self.flow_weights: Dict[str, float] = {}
        self._validate_weights()

    def _validate_weights(self) -> None:
        """가중치 합계 검증: trend + risk + Σflow = 1.0"""
        for regime, weights in self.REGIME_WEIGHTS.items():
            trend: float = float(weights.get("trend", 0))
            risk: float = float(weights.get("risk", 0))
            flow_dict: Dict[str, float] = weights.get("flow", {})
            flow: float = sum(float(v) for v in flow_dict.values())
            total: float = trend + risk + flow

            if abs(total - 1.0) > 0.01:
                logger.warning("[%s] 가중치 합계 오차: %.2f (목표: 1.0) — 자동 보정", regime, total)
                if flow > 0:
                    scale: float = (1.0 - trend - risk) / flow
                    for k in flow_dict:
                        weights["flow"][k] = float(flow_dict[k]) * scale

    def calculate(self, market_state: Dict[str, Any]) -> Dict[str, Any]:
        regime: str = str(market_state.get("regime", "Sideways"))
        regime_weights: Dict[str, Any] = self.REGIME_WEIGHTS.get(regime, self.REGIME_WEIGHTS["Sideways"])
        flow_data: Dict[str, Any] = market_state.get("flow", {})
        flow_score: float = self._calculate_flow_score(flow_data, regime_weights["flow"])

        return {
            "regime": regime,
            "trend_weight": regime_weights["trend"],
            "risk_weight": regime_weights["risk"],
            "flow_weight": sum(float(v) for v in regime_weights["flow"].values()),
            "flow_breakdown": regime_weights["flow"],
            "flow_score": flow_score,
            "weights_summary": self._get_summary(regime, regime_weights),
        }

    def _calculate_flow_score(self, flow_data: Dict[str, Any], flow_weights: Dict[str, float]) -> float:
        score: float = 0.0
        score += self._normalize(float(flow_data.get("foreigner_net", 0)), 100) * float(flow_weights.get("foreigner", 0.20))
        score += self._normalize(float(flow_data.get("institution_net", 0)), 100) * float(flow_weights.get("institution", 0.20))
        score += self._normalize(float(flow_data.get("program_net", 0)), 50) * float(flow_weights.get("program", 0.08))
        score += self._normalize(float(flow_data.get("retail_net", 0)), 100) * float(flow_weights.get("retail", 0.07))
        return min(1.0, max(0.0, score))

    def _normalize(self, value: float, base: float) -> float:
        if base == 0:
            return 0.0
        return min(1.0, max(-1.0, value / base)) * 0.5 + 0.5

    def _get_summary(self, regime: str, weights: Dict[str, Any]) -> str:
        flow_weights: Dict[str, float] = weights["flow"]
        return (
            f"[{regime}] 트렌드 {weights['trend']:.0%} | 위험 {weights['risk']:.0%} | "
            f"수급 {sum(float(v) for v in flow_weights.values()):.0%} "
            f"(외국인 {float(flow_weights.get('foreigner', 0)):.0%}, 기관 {float(flow_weights.get('institution', 0)):.0%})"
        )
