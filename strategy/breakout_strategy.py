"""
strategy/breakout_strategy.py - v1.2 FINAL (config 기반 가중치)
- config에서 기본 가중치를 읽어옴 (strategy_default_breakout_weight)
- 52주 신고가/신저가, 거래량 급증, 변동성 돌파 기반
"""

from typing import Any

from .base_strategy import BaseStrategy, config

DEFAULT_WEIGHT_KEY = "strategy_default_breakout_weight"


class BreakoutStrategy(BaseStrategy):
    def __init__(self, weight: float = None):
        if weight is None:
            weight = config.get_float(DEFAULT_WEIGHT_KEY, 0.30)
        self._weight = weight

    @property
    def name(self) -> str:
        return "Breakout"

    @property
    def weight(self) -> float:
        return self._weight

    def analyze(self, data: dict[str, Any]) -> dict[str, Any]:
        price = self._safe_get(data, "price", 0.0)
        tech = data.get("tech_data", {})
        high_52w = self._safe_get(data, "high_52w", price * 1.2 if price > 0 else 0.0)
        low_52w = self._safe_get(data, "low_52w", price * 0.8 if price > 0 else 0.0)
        volume_ratio = self._safe_get(tech, "volume_ratio", 1.0)
        atr = self._safe_get(data, "atr", price * 0.02 if price > 0 else 0.0)
        regime = self._safe_get(data, "regime", "Sideways")

        if price <= 0 or high_52w <= 0:
            return {
                "score": 0.5,
                "action": "HOLD",
                "confidence": 0.3,
                "reason": "가격 또는 52주 데이터 부족",
                "details": {"high_52w": high_52w, "low_52w": low_52w},
            }

        score = 0.5
        action = "HOLD"
        confidence = 0.5
        reasons = []

        if price >= high_52w * 0.98:
            score += 0.25
            reasons.append("52주 신고가 근접 (돌파 임박)")
            if price >= high_52w:
                score += 0.15
                reasons.append("52주 신고가 돌파 (강한 모멘텀)")
        elif price <= low_52w * 1.02:
            score -= 0.25
            reasons.append("52주 신저가 근접 (하방 위험)")

        if volume_ratio > 2.0 and price > tech.get("ema20", price):
            score += 0.15
            reasons.append(f"거래량 급증 (×{volume_ratio:.1f})")
        elif volume_ratio > 1.5:
            score += 0.05
            reasons.append(f"거래량 증가 (×{volume_ratio:.1f})")

        if atr > 0 and price > 0:
            atr_ratio = atr / price
            if atr_ratio > 0.03:
                score += 0.10
                reasons.append(f"변동성 확대 (ATR {atr_ratio:.2%})")

        if regime == "Bull" and score > 0.6:
            score += 0.10
            reasons.append("상승 국면에서 모멘텀 가속")

        if score >= 0.75:
            action = "BUY"
            confidence = min(0.95, 0.55 + (score - 0.75) * 1.6)
        elif score <= 0.25:
            action = "SELL"
            confidence = min(0.95, 0.55 + (0.25 - score) * 1.6)
        else:
            action = "HOLD"
            confidence = 0.5 + abs(score - 0.5) * 0.5

        score = max(0.0, min(1.0, score))
        confidence = max(0.3, min(0.95, confidence))

        return {
            "score": score,
            "action": action,
            "confidence": confidence,
            "reason": " | ".join(reasons[:4]) if reasons else "돌파 중립",
            "details": {
                "high_52w": high_52w,
                "low_52w": low_52w,
                "volume_ratio": volume_ratio,
                "atr_ratio": atr / price if price > 0 else 0,
            },
        }
