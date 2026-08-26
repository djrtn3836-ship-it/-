"""
decision/hybrid_decider.py - v7.2.2 (액션 표준화)
- BUY/SELL/HOLD 영문 액션 반환
- 한국어 라벨은 별도 필드(action_label)로 분리
"""


class HybridDecider:
    def __init__(self):
        pass

    def decide(self, data: dict) -> dict:
        """
        점수(score) 기반 의사결정
        Returns:
            {
                "action": "BUY" | "SELL" | "HOLD",   # 🔥 영문 표준
                "action_label": "강력 매수" | ... ,   # 한국어 표시용
                "score": float,
                "confidence": float,
                "reasons": List[str],
                "risks": List[str],
                "counterfactuals": List[str]
            }
        """
        score = data.get("score", 0.5)

        # 🔥 영문 액션 + 한국어 라벨 분리
        if score >= 0.75:
            action, label = "BUY", "강력 매수"
        elif score >= 0.60:
            action, label = "BUY", "매수"
        elif score >= 0.45:
            action, label = "HOLD", "중립 (관망)"
        elif score >= 0.30:
            action, label = "SELL", "매도"
        elif score >= 0.15:
            action, label = "SELL", "부분 매도"
        else:
            action, label = "SELL", "전량 매도"

        confidence = min(0.95, 0.5 + score * 0.5)

        # 근거 생성 (기존 로직 유지)
        reasons = self._generate_reasons(data, action)
        risks = self._generate_risks(data, action)

        return {
            "action": action,
            "action_label": label,
            "score": score,
            "confidence": confidence,
            "reasons": reasons,
            "risks": risks,
            "counterfactuals": self._generate_counterfactuals(data, action),
        }

    def _generate_reasons(self, data: dict, action: str) -> list[str]:
        reasons = []
        if action in ["BUY"]:
            if data.get("macro", {}).get("score", 0) > 0.6:
                reasons.append("거시 경제 지표 양호")
            if data.get("sector", {}).get("score", 0) > 0.6:
                reasons.append("섹터 모멘텀 우위")
            if data.get("stock", {}).get("score", 0) > 0.6:
                reasons.append("개별 종목 강세")
        elif action in ["SELL"]:
            if data.get("macro", {}).get("score", 0) < 0.4:
                reasons.append("거시 경제 지표 부진")
            if data.get("sector", {}).get("score", 0) < 0.4:
                reasons.append("섹터 모멘텀 약세")
        return reasons or ["다중 팩터 우위"]

    def _generate_risks(self, data: dict, action: str) -> list[str]:
        risks = []
        if action in ["BUY"]:
            if data.get("stock", {}).get("volatility", 0) > 0.3:
                risks.append("변동성 높음")
        return risks or ["시장 변동성 주의"]

    def _generate_counterfactuals(self, data: dict, action: str) -> list[str]:
        return ["대체 시나리오 분석 필요"]
