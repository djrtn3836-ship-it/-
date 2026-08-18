"""
strategy/reversal_strategy.py - v1.0 FINAL (역추세/과매수과매도 전략)
- RSI 과매수/과매도, 볼린저 밴드, 이격도 기반
- 하락장에서 반등 신호 포착
"""

from typing import Dict, Any
from .base_strategy import BaseStrategy

class ReversalStrategy(BaseStrategy):
    def __init__(self, weight: float = 0.30):
        self._weight = weight

    @property
    def name(self) -> str:
        return "Reversal"

    @property
    def weight(self) -> float:
        return self._weight

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        price = data.get('price', 0.0)
        tech = data.get('tech_data', {})
        rsi = tech.get('rsi', 50)
        ema20 = tech.get('ema20', price)
        bb_upper = data.get('bb_upper', price * 1.05)
        bb_lower = data.get('bb_lower', price * 0.95)
        regime = data.get('regime', 'Sideways')

        score = 0.5
        action = 'HOLD'
        confidence = 0.5
        reasons = []

        # 1. RSI (과매수/과매도)
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

        # 2. 볼린저 밴드 하단/상단 이탈
        if bb_lower > 0 and price <= bb_lower:
            score += 0.15
            reasons.append("볼린저 하단 이탈 (반등 신호)")
        elif bb_upper > 0 and price >= bb_upper:
            score -= 0.15
            reasons.append("볼린저 상단 돌파 (과열)")

        # 3. 이격도 (20일선 대비)
        if ema20 > 0:
            gap = (price - ema20) / ema20 * 100
            if gap < -5:
                score += 0.15
                reasons.append(f"이격도 {gap:.1f}% (과도 하락)")
            elif gap > 5:
                score -= 0.15
                reasons.append(f"이격도 {gap:.1f}% (과도 상승)")

        # 4. 국면 조정 (Bear에서 반등 강화)
        if regime in ['Bear', 'Panic'] and score > 0.6:
            score += 0.10
            reasons.append("하락 국면에서 반등 기회")

        # 5. 액션 결정
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
                'gap_pct': gap if ema20 > 0 else 0,
            }
        }