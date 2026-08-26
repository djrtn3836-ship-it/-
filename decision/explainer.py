"""
Explainer v5.1.2 — Claude 피드백 반영 (Counterfactual 분석 추가)

변경사항:
1. Why NOT 구조를 Counterfactual 분석으로 강화
2. "만약 매수했다면?" 시나리오 추가
3. 반사실적(Counterfactual) 근거 명시
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CounterfactualAnalysis:
    """반사실적 분석 (Counterfactual)"""

    scenario: str  # 'BUY', 'SELL', 'HOLD'
    action_taken: str  # 실제 행동
    reasoning: list[str]  # 근거
    estimated_impact: float | None = None  # 추정 영향
    confidence: float = 0.5


class Explainer:
    """
    설명 생성기 v5.1.2 — Counterfactual 분석 포함

    Claude 피드백 반영:
    - "왜 샀는가" (Why Now) + "왜 안 샀는가" (Why NOT)
    - Counterfactual 분석: "만약 매수했다면?"
    """

    def __init__(self):
        self.counterfactuals: list[CounterfactualAnalysis] = []

    def explain(self, decision: dict, data: dict) -> dict:
        """
        판단 설명 생성 (Counterfactual 포함)

        Returns:
            {
                'summary': str,
                'positives': List[str],
                'negatives': List[str],
                'counterfactuals': List[CounterfactualAnalysis],
                'recommendation': str
            }
        """

        # 1. 기본 설명
        positives = self._extract_positives(decision, data)
        negatives = self._extract_negatives(decision, data)

        # 2. Counterfactual 분석 (Claude 피드백 반영)
        counterfactuals = self._generate_counterfactuals(decision, data)

        # 3. 종합 요약
        summary = self._generate_summary(decision, positives, negatives)

        return {
            "summary": summary,
            "positives": positives,
            "negatives": negatives,
            "counterfactuals": counterfactuals,
            "recommendation": self._get_recommendation(decision),
        }

    def _generate_counterfactuals(self, decision: dict, data: dict) -> list[CounterfactualAnalysis]:
        """
        Counterfactual 분석 생성 (Claude 피드백)

        "만약 매수했다면?" 시나리오 분석
        """
        counterfactuals = []
        action = decision.get("action", "HOLD")

        # 시나리오 1: 매수 시나리오 (반사실적)
        buy_scenario = CounterfactualAnalysis(
            scenario="BUY",
            action_taken=action,
            reasoning=self._get_buy_counterfactual_reasons(decision, data),
            estimated_impact=self._estimate_buy_impact(decision, data),
            confidence=0.6,
        )
        counterfactuals.append(buy_scenario)

        # 시나리오 2: 매도 시나리오 (반사실적)
        sell_scenario = CounterfactualAnalysis(
            scenario="SELL",
            action_taken=action,
            reasoning=self._get_sell_counterfactual_reasons(decision, data),
            estimated_impact=self._estimate_sell_impact(decision, data),
            confidence=0.6,
        )
        counterfactuals.append(sell_scenario)

        # 시나리오 3: 중립 시나리오 (반사실적)
        hold_scenario = CounterfactualAnalysis(
            scenario="HOLD",
            action_taken=action,
            reasoning=self._get_hold_counterfactual_reasons(decision, data),
            estimated_impact=0.0,
            confidence=0.5,
        )
        counterfactuals.append(hold_scenario)

        self.counterfactuals = counterfactuals
        return counterfactuals

    def _get_buy_counterfactual_reasons(self, decision: dict, data: dict) -> list[str]:
        """매수 시나리오 근거 생성"""
        reasons = []

        if decision.get("score", 0) > 0.6:
            reasons.append("현재 점수(%.0f)는 매수 기준을 초과함" % (decision.get("score", 0) * 100))

        if data.get("volume_ratio", 1) > 1.2:
            reasons.append("거래량이 평균 대비 %.0f%% 증가함" % ((data.get("volume_ratio", 1) - 1) * 100))

        if data.get("foreigner_net", 0) > 0:
            reasons.append("외국인 순매수 중 (%.0f억원)" % data.get("foreigner_net", 0))

        if not reasons:
            reasons.append("매수 모멘텀 부족")

        return reasons

    def _get_sell_counterfactual_reasons(self, decision: dict, data: dict) -> list[str]:
        """매도 시나리오 근거 생성"""
        reasons = []

        if decision.get("score", 0) < 0.4:
            reasons.append("현재 점수(%.0f)는 매도 기준에 해당함" % (decision.get("score", 0) * 100))

        if data.get("per", 0) > data.get("sector_avg_per", 30):
            reasons.append(
                "PER(%.0f)가 업종 평균(%.0f) 대비 고평가" % (data.get("per", 0), data.get("sector_avg_per", 30))
            )

        if data.get("institution_net", 0) < 0:
            reasons.append("기관 순매도 중 (%.0f억원)" % abs(data.get("institution_net", 0)))

        if not reasons:
            reasons.append("매도 모멘텀 부족")

        return reasons

    def _get_hold_counterfactual_reasons(self, decision: dict, data: dict) -> list[str]:
        """중립 시나리오 근거 생성"""
        reasons = []

        if 0.4 <= decision.get("score", 0.5) <= 0.6:
            reasons.append("현재 점수(%.0f%%)는 중립 구간 (40~60%%)" % (decision.get("score", 0.5) * 100))

        if data.get("regime") in ["Sideways", "Correction"]:
            reasons.append("현재 시장 국면(%s)은 진입에 불확실성이 높음" % data.get("regime", "Unknown"))

        if not reasons:
            reasons.append("추가 신호 대기 중")

        return reasons

    def _estimate_buy_impact(self, decision: dict, data: dict) -> float:
        """매수 시 추정 영향"""
        base = decision.get("score", 0.5) * 0.05  # 0~5%

        # 레짐 조정
        regime = data.get("regime", "Sideways")
        regime_multipler = {"Bull": 1.2, "Sideways": 1.0, "Bear": 0.6, "Panic": 0.3, "Recovery": 1.1}.get(regime, 1.0)

        return base * regime_multipler

    def _estimate_sell_impact(self, decision: dict, data: dict) -> float:
        """매도 시 추정 영향"""
        return -self._estimate_buy_impact(decision, data)
