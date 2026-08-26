"""
strategy/trend_strategy.py - v1.2 FINAL (config 기반 가중치)
- config에서 기본 가중치를 읽어옴 (strategy_default_trend_weight)
- EMA 정배열/역배열, ADX, 20일선 이탈/돌파 기반
"""

from typing import Any

from .base_strategy import BaseStrategy, config

DEFAULT_WEIGHT_KEY = "strategy_default_trend_weight"


class TrendStrategy(BaseStrategy):
    def __init__(self, weight: float = None):
        if weight is None:
            weight = config.get_float(DEFAULT_WEIGHT_KEY, 0.40)
        self._weight = weight

    @property
    def name(self) -> str:
        return "Trend"

    @property
    def weight(self) -> float:
        return self._weight

    def analyze(self, data: dict[str, Any]) -> dict[str, Any]:
        price = self._safe_get(data, "price", 0.0)
        tech = data.get("tech_data", {})
        ema5 = self._safe_get(tech, "ema5", price)
        ema20 = self._safe_get(tech, "ema20", price)
        ema60 = self._safe_get(tech, "ema60", price)
        adx = self._safe_get(data, "adx", 20.0)
        volume_ratio = self._safe_get(tech, "volume_ratio", 1.0)
        regime = self._safe_get(data, "regime", "Sideways")

        if price <= 0 or ema20 <= 0:
            return {
                "score": 0.5,
                "action": "HOLD",
                "confidence": 0.3,
                "reason": "가격 또는 이평선 데이터 부족",
                "details": {"ema5": ema5, "ema20": ema20, "ema60": ema60},
            }

        score = 0.5
        action = "HOLD"
        confidence = 0.5
        reasons = []

        if ema5 > ema20 > ema60:
            score += 0.25
            reasons.append("EMA 정배열 (강한 상승 추세)")
            if regime in ["Bull", "Recovery"]:
                score += 0.15
                reasons.append("상승 국면과 추세 동조")
        elif ema5 > ema20:
            score += 0.10
            reasons.append("단기 상승 추세")
        elif ema5 < ema20 < ema60:
            score -= 0.25
            reasons.append("EMA 역배열 (강한 하락 추세)")
        elif ema5 < ema20:
            score -= 0.10
            reasons.append("단기 하락 추세")

        if adx > 30:
            score += 0.10
            reasons.append(f"ADX {adx:.0f} (강한 추세)")
        elif adx > 20:
            score += 0.05
            reasons.append(f"ADX {adx:.0f} (보통 추세)")
        else:
            score -= 0.05
            reasons.append(f"ADX {adx:.0f} (추세 약함)")

        if price > ema20:
            score += 0.10
            reasons.append("20일선 상회 (지지)")
        elif price < ema20:
            score -= 0.10
            reasons.append("20일선 하회 (저항)")

        if volume_ratio > 1.5 and score > 0.6:
            score += 0.05
            reasons.append("거래량 동반 상승 (추세 지속)")

        if score >= 0.70:
            action = "BUY"
            confidence = min(0.95, 0.6 + (score - 0.7) * 1.5)
        elif score <= 0.30:
            action = "SELL"
            confidence = min(0.95, 0.6 + (0.3 - score) * 1.5)
        else:
            action = "HOLD"
            confidence = 0.5 + abs(score - 0.5)

        score = max(0.0, min(1.0, score))
        confidence = max(0.3, min(0.95, confidence))

        return {
            "score": score,
            "action": action,
            "confidence": confidence,
            "reason": " | ".join(reasons[:4]) if reasons else "추세 중립",
            "details": {
                "ema5": ema5,
                "ema20": ema20,
                "ema60": ema60,
                "adx": adx,
                "volume_ratio": volume_ratio,
            },
        }
