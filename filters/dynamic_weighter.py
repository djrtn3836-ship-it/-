"""
Dynamic Weighter v5.1.2 — Claude 피드백 반영 (수급 지표 비중 명시화)

변경사항:
1. 외국인/기관 수급 가중치 명시화
2. Regime별 수급 가중치 차등 적용
3. 수급 지표의 영향력 문서화
4. 가중치 합계 1.0으로 정확히 조정 (Claude 피드백 반영)
"""

import logging

logger = logging.getLogger(__name__)


class DynamicWeighter:
    """
    동적 가중치 계산기 v5.1.2

    수급 지표 가중치 (Claude 피드백 반영):
    - 외국인 순매수: Regime별 10~20%
    - 기관 순매수: Regime별 10~15%
    - 프로그램 매매: Regime별 5~8%
    """

    # ============================================================
    # Regime별 수급 가중치 (Claude 피드백 반영) — 합계 1.0 정확히
    # ============================================================

    REGIME_WEIGHTS = {
        "Bull": {
            "trend": 0.30,
            "risk": 0.25,
            "flow": {
                "foreigner": 0.20,
                "institution": 0.12,
                "program": 0.08,
                "retail": 0.05,
                # flow 합계 = 0.45 → 총합 0.30 + 0.25 + 0.45 = 1.00 ✅
            },
        },
        "Sideways": {
            "trend": 0.30,
            "risk": 0.30,
            "flow": {
                "foreigner": 0.15,
                "institution": 0.12,
                "program": 0.08,
                "retail": 0.05,
                # flow 합계 = 0.40 → 총합 0.30 + 0.30 + 0.40 = 1.00 ✅
            },
        },
        "Bear": {
            "trend": 0.25,
            "risk": 0.40,
            "flow": {
                "foreigner": 0.12,
                "institution": 0.15,
                "program": 0.05,
                "retail": 0.03,
                # flow 합계 = 0.35 → 총합 0.25 + 0.40 + 0.35 = 1.00 ✅
            },
        },
        "Panic": {
            "trend": 0.15,
            "risk": 0.50,
            "flow": {
                "foreigner": 0.10,
                "institution": 0.15,
                "program": 0.05,
                "retail": 0.05,
                # flow 합계 = 0.35 → 총합 0.15 + 0.50 + 0.35 = 1.00 ✅
            },
        },
        "Recovery": {
            "trend": 0.30,
            "risk": 0.25,
            "flow": {
                "foreigner": 0.20,
                "institution": 0.12,
                "program": 0.08,
                "retail": 0.05,
                # flow 합계 = 0.45 → 총합 0.30 + 0.25 + 0.45 = 1.00 ✅
            },
        },
    }

    def __init__(self):
        self.flow_weights = {}
        self._validate_weights()

    def _validate_weights(self):
        """가중치 합계 검증 (1.0 = 100%)"""
        for regime, weights in self.REGIME_WEIGHTS.items():
            trend = weights.get("trend", 0)
            risk = weights.get("risk", 0)
            flow = sum(weights.get("flow", {}).values())
            total = trend + risk + flow

            if abs(total - 1.0) > 0.01:
                logger.warning(f"[{regime}] 가중치 합계 오차: {total:.2f} (목표: 1.0) — 자동 보정")
                # 자동 보정: flow 비율 조정
                if total > 1.0:
                    scale = (1.0 - trend - risk) / flow if flow > 0 else 1.0
                    for k in weights["flow"].keys():
                        weights["flow"][k] *= scale
                elif total < 1.0:
                    scale = (1.0 - trend - risk) / flow if flow > 0 else 1.0
                    for k in weights["flow"].keys():
                        weights["flow"][k] *= scale

    def calculate(self, market_state: dict) -> dict:
        """시장 상태 기반 동적 가중치 계산"""
        regime = market_state.get("regime", "Sideways")
        regime_weights = self.REGIME_WEIGHTS.get(regime, self.REGIME_WEIGHTS["Sideways"])

        flow_data = market_state.get("flow", {})
        flow_score = self._calculate_flow_score(flow_data, regime_weights["flow"])

        return {
            "regime": regime,
            "trend_weight": regime_weights["trend"],
            "risk_weight": regime_weights["risk"],
            "flow_weight": sum(regime_weights["flow"].values()),
            "flow_breakdown": regime_weights["flow"],
            "flow_score": flow_score,
            "weights_summary": self._get_summary(regime, regime_weights),
        }

    def _calculate_flow_score(self, flow_data: dict, flow_weights: dict) -> float:
        """수급 점수 계산"""
        score = 0.0

        foreigner = flow_data.get("foreigner_net", 0)
        foreigner_score = self._normalize(foreigner, 100)
        score += foreigner_score * flow_weights.get("foreigner", 0.20)

        institution = flow_data.get("institution_net", 0)
        institution_score = self._normalize(institution, 100)
        score += institution_score * flow_weights.get("institution", 0.20)

        program = flow_data.get("program_net", 0)
        program_score = self._normalize(program, 50)
        score += program_score * flow_weights.get("program", 0.08)

        retail = flow_data.get("retail_net", 0)
        retail_score = self._normalize(retail, 100)
        score += retail_score * flow_weights.get("retail", 0.07)

        return min(1.0, max(0.0, score))

    def _normalize(self, value: float, base: float) -> float:
        if base == 0:
            return 0.0
        return min(1.0, max(-1.0, value / base)) * 0.5 + 0.5

    def _get_summary(self, regime: str, weights: dict) -> str:
        flow_weights = weights["flow"]
        return (
            f"[{regime}] 트렌드 {weights['trend']:.0%} | "
            f"위험 {weights['risk']:.0%} | "
            f"수급 {sum(flow_weights.values()):.0%} "
            f"(외국인 {flow_weights.get('foreigner', 0):.0%}, "
            f"기관 {flow_weights.get('institution', 0):.0%})"
        )
