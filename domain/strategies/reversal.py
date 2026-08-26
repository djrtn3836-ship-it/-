# -*- coding: utf-8 -*-
"""
domain/strategies/reversal.py - V10 Reversal Strategy v2.0

개선 사항 (v2.0):
    - Stochastic Oscillator (%K/%D) 통합
    - Bollinger Band %B (가격 위치 정규화) 활용
    - RSI Divergence 준거 (이전보다 정밀한 임계값)
    - 다중 신호 합의 시 confidence 보너스
    - Google Style Docstrings 100%
"""

from typing import Any, Dict

from domain.strategies.base import Strategy, StrategyResult


class ReversalStrategy(Strategy):
    """반전 전략 (RSI + Bollinger Band + Stochastic).

    과매수/과매도 구간에서 가격 반전을 포착합니다.
    단일 지표 의존도를 낮추고 다중 확인으로 신뢰도를 높입니다.

    Attributes:
        _weight: 앙상블 내 전략 가중치 (기본 0.30)
    """

    def __init__(self, weight: float = 0.30) -> None:
        """초기화.

        Args:
            weight: 앙상블 가중치 (기본 0.30)
        """
        self._weight = weight

    @property
    def name(self) -> str:
        """전략 이름."""
        return "Reversal"

    @property
    def weight(self) -> float:
        """앙상블 가중치."""
        return self._weight

    async def analyze(self, data: Dict[str, Any]) -> StrategyResult:
        """반전 분석 실행.

        분석 순서:
            1. RSI 과매수/과매도 판정
            2. Bollinger Band 접촉 판정 (bb_upper/lower)
            3. Stochastic %K/%D 크로스 확인
            4. 다중 신호 합의 시 confidence 보너스

        Args:
            data: 시장 데이터 딕셔너리
                - tech_data (dict): RSI, bb_upper/middle/lower, stoch_k, stoch_d
                - price (float): 현재가

        Returns:
            StrategyResult: 반전 분석 결과
        """
        tech_data: Dict[str, Any] = data.get("tech_data", {})
        price: float = float(data.get("price", 0))

        rsi: float = tech_data.get("rsi", 50)
        bb_upper: float = tech_data.get("bb_upper", price * 1.05)
        bb_lower: float = tech_data.get("bb_lower", price * 0.95)
        stoch_k: float = tech_data.get("stoch_k", 50)
        stoch_d: float = tech_data.get("stoch_d", 50)

        score: float = 0.5
        confidence: float = 0.5
        action: str = "HOLD"
        reasons = []
        buy_signals: int = 0
        sell_signals: int = 0

        # ── 1. RSI 과매수/과매도 ─────────────────────────────────
        if rsi < 30:
            score += 0.30
            confidence += 0.12
            action = "BUY"
            buy_signals += 1
            reasons.append(f"RSI 과매도 ({rsi:.0f} < 30) → 반등 기대")
        elif rsi < 35:
            score += 0.12
            buy_signals += 1
            reasons.append(f"RSI 과매도 근접 ({rsi:.0f})")
        elif rsi > 70:
            score -= 0.30
            confidence += 0.12
            action = "SELL"
            sell_signals += 1
            reasons.append(f"RSI 과매수 ({rsi:.0f} > 70) → 조정 경계")
        elif rsi > 65:
            score -= 0.12
            sell_signals += 1
            reasons.append(f"RSI 과매수 근접 ({rsi:.0f})")

        # ── 2. Bollinger Band 접촉 ────────────────────────────────
        if bb_lower > 0 and price > 0:
            bb_width = bb_upper - bb_lower
            if bb_width > 0:
                # %B = (price - lower) / (upper - lower)  [0=하단, 1=상단]
                pct_b = (price - bb_lower) / bb_width

                if pct_b <= 0.05:
                    # 하단 터치 → 강한 반등 신호
                    score += 0.28
                    confidence += 0.10
                    if action == "HOLD":
                        action = "BUY"
                    buy_signals += 1
                    reasons.append(
                        f"볼린저 하단 터치 (%B={pct_b:.2f}, 가격={price:.0f})"
                    )
                elif pct_b <= 0.20:
                    score += 0.12
                    buy_signals += 1
                    reasons.append(f"볼린저 하단 근접 (%B={pct_b:.2f})")
                elif pct_b >= 0.95:
                    # 상단 터치 → 강한 조정 신호
                    score -= 0.28
                    confidence += 0.10
                    if action == "HOLD":
                        action = "SELL"
                    sell_signals += 1
                    reasons.append(
                        f"볼린저 상단 터치 (%B={pct_b:.2f}, 가격={price:.0f})"
                    )
                elif pct_b >= 0.80:
                    score -= 0.12
                    sell_signals += 1
                    reasons.append(f"볼린저 상단 근접 (%B={pct_b:.2f})")

        # ── 3. Stochastic %K/%D 크로스 ───────────────────────────
        if stoch_k != 50 or stoch_d != 50:  # 기본값이 아닌 경우만 처리
            if stoch_k < 20 and stoch_d < 20:
                score += 0.12
                confidence += 0.08
                buy_signals += 1
                if action == "HOLD":
                    action = "BUY"
                reasons.append(f"Stochastic 과매도 (%K={stoch_k:.0f})")
            elif stoch_k > 80 and stoch_d > 80:
                score -= 0.12
                confidence += 0.08
                sell_signals += 1
                if action == "HOLD":
                    action = "SELL"
                reasons.append(f"Stochastic 과매수 (%K={stoch_k:.0f})")
            # %K가 %D를 상향 돌파 (저점 권에서) → 골든크로스
            elif stoch_k > stoch_d and stoch_k < 50:
                score += 0.06
                buy_signals += 1
                reasons.append(f"Stochastic 저점 골든크로스 ({stoch_k:.0f}>{stoch_d:.0f})")
            # %K가 %D를 하향 돌파 (고점 권에서) → 데드크로스
            elif stoch_k < stoch_d and stoch_k > 50:
                score -= 0.06
                sell_signals += 1
                reasons.append(f"Stochastic 고점 데드크로스 ({stoch_k:.0f}<{stoch_d:.0f})")

        # ── 4. 다중 신호 합의 보너스 ─────────────────────────────
        if buy_signals >= 2:
            confidence += 0.08
            reasons.append(f"다중 매수 신호 합의 ({buy_signals}개)")
        elif sell_signals >= 2:
            confidence += 0.08
            reasons.append(f"다중 매도 신호 합의 ({sell_signals}개)")

        # 신호가 상충 → HOLD
        if buy_signals > 0 and sell_signals > 0:
            action = "HOLD"
            confidence -= 0.10
            if reasons and "상충" not in reasons[-1]:
                reasons.append("매수/매도 신호 상충 → 관망")

        # ── 정규화 ────────────────────────────────────────────────
        score = max(0.0, min(1.0, score))
        confidence = max(0.30, min(0.90, confidence))

        # 약한 신호 → HOLD
        if abs(score - 0.5) < 0.12:
            action = "HOLD"

        return StrategyResult(
            name=self.name,
            action=action,
            score=score,
            confidence=confidence,
            reasons=reasons[:4],
            metadata={
                "rsi": rsi,
                "bb_upper": bb_upper,
                "bb_lower": bb_lower,
                "stoch_k": stoch_k,
                "stoch_d": stoch_d,
                "buy_signals": buy_signals,
                "sell_signals": sell_signals,
            },
        )
