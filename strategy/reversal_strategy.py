"""
strategy/reversal_strategy.py - v1.2 FINAL (config 기반 가중치)
- config에서 기본 가중치를 읽어옴 (strategy_default_reversal_weight)
- RSI 과매수/과매도, 볼린저 밴드, 이격도 기반
"""

from typing import Dict, Any
from .base_strategy import BaseStrategy, config

DEFAULT_WEIGHT_KEY = "strategy_default_reversal_weight"


class ReversalStrategy(BaseStrategy):
    def __init__(self, weight: float = None):
        if weight is None:
            weight = config.get_float(DEFAULT_WEIGHT_KEY, 0.30)
        self._weight = weight

    @property
    def name(self) -> str:
        return "Reversal"

    @property
    def weight(self) -> float:
        return self._weight

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        price = self._safe_get(data, 'price', 0.0)
        tech = data.get('tech_data', {})
        rsi = self._safe_get(tech, 'rsi', 50.0)
        ema20 = self._safe_get(tech, 'ema20', price)
        bb_upper = self._safe_get(data, 'bb_upper', price * 1.05 if price > 0 else 0.0)
        bb_lower = self._safe_get(data, 'bb_lower', price * 0.95 if price > 0 else 0.0)
        regime = self._safe_get(data, 'regime', 'Sideways')

        if price <= 0:
            return {
                'score': 0.5,
                'action': 'HOLD',
                'confidence': 0.3,
                'reason': '가격 데이터 부족',
                'details': {'rsi': rsi}
            }

        score = 0.5
        action = 'HOLD'
        confidence = 0.5
        reasons = []
        gap = 0.0

        if rsi < 25:
            score += 0.30
            reasons.append(f"RSI {rsi:.0f} (과매도, 반등 기대)")
        elif rsi < 30:
            score += 0.20
            reasons.append(f"RSI {rsi:.0f} (과매도 임박)")
        elif rsi > 75:
            score -= 0.30
            reasons.append(f"RSI {rsi:.0f} (과매수, 조정 위험)")
        elif rsi > 70:
            score -= 0.20
            reasons.append(f"RSI {rsi:.0f} (과매수 임박)")

        if bb_lower > 0 and price <= bb_lower:
            score += 0.15
            reasons.append("볼린저 하단 이탈 (반등 신호)")
        elif bb_upper > 0 and price >= bb_upper:
            score -= 0.15
            reasons.append("볼린저 상단 돌파 (과열)")

        if ema20 > 0:
            gap = (price - ema20) / ema20 * 100
            if gap < -5:
                score += 0.15
                reasons.append(f"이격도 {gap:.1f}% (과도 하락)")
            elif gap > 5:
                score -= 0.15
                reasons.append(f"이격도 {gap:.1f}% (과도 상승)")

        if regime in ['Bear', 'Panic'] and score > 0.6:
            score += 0.10
            reasons.append("하락 국면에서 반등 기회")

        if score >= 0.70:
            action = 'BUY'
            confidence = min(0.9, 0.5 + (score - 0.7) * 1.2)
        elif score <= 0.30:
            action = 'SELL'
            confidence = min(0.9, 0.5 + (0.3 - score) * 1.2)
        else:
            action = 'HOLD'
            confidence = 0.5 + abs(score - 0.5) * 0.5

        score = max(0.0, min(1.0, score))
        confidence = max(0.3, min(0.9, confidence))

        return {
            'score': score,
            'action': action,
            'confidence': confidence,
            'reason': ' | '.join(reasons[:4]) if reasons else '역추세 중립',
            'details': {
                'rsi': rsi,
                'bb_upper': bb_upper,
                'bb_lower': bb_lower,
                'gap_pct': gap,
            }
        }