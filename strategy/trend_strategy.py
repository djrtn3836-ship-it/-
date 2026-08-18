"""
strategy/trend_strategy.py - v1.0 FINAL (추세 추종 전략)
- EMA 정배열/역배열, ADX, 20일선 이탈/돌파 기반
- 상승장에서 강한 매수 신호
"""

from typing import Dict, Any
from .base_strategy import BaseStrategy

class TrendStrategy(BaseStrategy):
    def __init__(self, weight: float = 0.40):
        self._weight = weight

    @property
    def name(self) -> str:
        return "Trend"

    @property
    def weight(self) -> float:
        return self._weight

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        price = data.get('price', 0.0)
        tech = data.get('tech_data', {})
        ema5 = tech.get('ema5', price)
        ema20 = tech.get('ema20', price)
        ema60 = tech.get('ema60', price)
        adx = data.get('adx', 20)
        volume_ratio = tech.get('volume_ratio', 1.0)
        regime = data.get('regime', 'Sideways')

        score = 0.5
        action = 'HOLD'
        confidence = 0.5
        reasons = []

        # 1. EMA 정배열 (Bullish)
        if ema5 > ema20 > ema60:
            score += 0.25
            reasons.append("EMA 정배열 (강한 상승 추세)")
            if regime in ['Bull', 'Recovery']:
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

        # 2. ADX (추세 강도)
        if adx > 30:
            score += 0.10
            reasons.append(f"ADX {adx:.0f} (강한 추세)")
        elif adx > 20:
            score += 0.05
            reasons.append(f"ADX {adx:.0f} (보통 추세)")
        else:
            score -= 0.05
            reasons.append(f"ADX {adx:.0f} (추세 약함)")

        # 3. 20일선 이탈/돌파
        if price > ema20 and ema20 > 0:
            score += 0.10
            reasons.append("20일선 상회 (지지)")
        elif price < ema20 and ema20 > 0:
            score -= 0.10
            reasons.append("20일선 하회 (저항)")

        # 4. 거래량 확인 (추세 지속성)
        if volume_ratio > 1.5 and score > 0.6:
            score += 0.05
            reasons.append("거래량 동반 상승 (추세 지속)")

        # 5. 액션 결정
        if score >= 0.70:
            action = 'BUY'
            confidence = min(0.95, 0.6 + (score - 0.7) * 1.5)
        elif score <= 0.30:
            action = 'SELL'
            confidence = min(0.95, 0.6 + (0.3 - score) * 1.5)
        else:
            action = 'HOLD'
            confidence = 0.5 + abs(score - 0.5)

        score = max(0.0, min(1.0, score))
        confidence = max(0.3, min(0.95, confidence))

        return {
            'score': score,
            'action': action,
            'confidence': confidence,
            'reason': ' | '.join(reasons[:4]) if reasons else '추세 중립',
            'details': {
                'ema5': ema5,
                'ema20': ema20,
                'ema60': ema60,
                'adx': adx,
                'volume_ratio': volume_ratio,
            }
        }