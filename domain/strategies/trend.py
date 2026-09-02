# -*- coding: utf-8 -*-
"""
domain/strategies/trend.py - V10 Trend Following Strategy v2.0.1

v2.0 → v2.0.1 (Session 21):
    - reasons: List[str] 타입 어노테이션 명시 (mypy strict 준수)
    - 로직 변경 없음
"""

from typing import Any, Dict, List

from domain.strategies.base import Strategy, StrategyResult


class TrendStrategy(Strategy):
    """추세 추종 전략 (EMA 정배열 + MACD 확인).

    EMA 5/20/60 정배열을 기본 신호로 사용하고,
    MACD 크로스오버로 신호 강도를 확인합니다.

    Attributes:
        _weight: 앙상블 내 전략 가중치 (기본 0.40)
    """

    def __init__(self, weight: float = 0.40) -> None:
        """초기화.

        Args:
            weight: 앙상블 가중치 (기본 0.40)
        """
        self._weight = weight

    @property
    def name(self) -> str:
        """전략 이름."""
        return "Trend"

    @property
    def weight(self) -> float:
        """앙상블 가중치."""
        return self._weight

    async def analyze(self, data: Dict[str, Any]) -> StrategyResult:
        """추세 분석 실행.

        분석 순서:
            1. EMA 정배열/역배열 판정 (기본 신호)
            2. RSI 과매수/과매도 필터
            3. MACD 크로스오버 확인 (보조 신호)
            4. Regime 보정 (Bullish → BUY 보너스)

        Args:
            data: 시장 데이터 딕셔너리
                - tech_data (dict): EMA5/20/60, RSI, MACD, MACD_Signal
                - regime (str): 시장 레짐

        Returns:
            StrategyResult: 추세 분석 결과
        """
        tech_data: Dict[str, Any] = data.get("tech_data", {})
        regime: str = data.get("regime", "Sideways")

        if not tech_data:
            return StrategyResult(
                name=self.name,
                action="HOLD",
                score=0.5,
                confidence=0.3,
                reasons=["Insufficient technical data"],
            )

        ema5: float = tech_data.get("ema5", 0)
        ema20: float = tech_data.get("ema20", 0)
        ema60: float = tech_data.get("ema60", 0)
        rsi: float = tech_data.get("rsi", 50)
        macd: float = tech_data.get("macd", 0.0)
        macd_signal: float = tech_data.get("macd_signal", 0.0)
        macd_hist: float = tech_data.get("macd_hist", 0.0)

        score: float = 0.5
        confidence: float = 0.5
        action: str = "HOLD"
        reasons: List[str] = []  # 🔧 mypy strict: List[str] 명시

        # ── 1. EMA 정배열/역배열 판정 ─────────────────────────────
        if ema5 > 0 and ema20 > 0 and ema60 > 0:
            if ema5 > ema20 > ema60:
                score += 0.28
                confidence += 0.20
                action = "BUY"
                reasons.append(f"EMA 정배열 ({ema5:.0f}>{ema20:.0f}>{ema60:.0f})")
            elif ema5 < ema20 < ema60:
                score -= 0.28
                confidence += 0.20
                action = "SELL"
                reasons.append(f"EMA 역배열 ({ema5:.0f}<{ema20:.0f}<{ema60:.0f})")
            elif ema5 > ema20:
                score += 0.14
                confidence += 0.10
                action = "BUY"
                reasons.append(f"단기 상승 (EMA5:{ema5:.0f}>EMA20:{ema20:.0f})")
            elif ema5 < ema20:
                score -= 0.14
                confidence += 0.10
                action = "SELL"
                reasons.append(f"단기 하락 (EMA5:{ema5:.0f}<EMA20:{ema20:.0f})")

        # ── 2. RSI 과매수/과매도 필터 ─────────────────────────────
        if rsi > 70:
            score -= 0.18
            reasons.append(f"RSI 과매수 ({rsi:.0f})")
            if action == "BUY":
                action = "HOLD"
                confidence -= 0.15
        elif rsi > 60 and action == "BUY":
            confidence -= 0.05
            reasons.append(f"RSI 주의구간 ({rsi:.0f})")
        elif rsi < 30:
            score += 0.18
            reasons.append(f"RSI 과매도 ({rsi:.0f})")
            if action == "SELL":
                action = "HOLD"
                confidence -= 0.15
        elif rsi < 40 and action == "SELL":
            confidence -= 0.05
            reasons.append(f"RSI 주의구간 ({rsi:.0f})")

        # ── 3. MACD 크로스오버 확인 (보조 신호) ──────────────────
        if macd != 0.0 or macd_signal != 0.0:
            if macd > macd_signal and macd_hist > 0:
                score += 0.08
                confidence += 0.08
                reasons.append(f"MACD 골든크로스 (hist={macd_hist:+.1f})")
            elif macd < macd_signal and macd_hist < 0:
                score -= 0.08
                confidence += 0.08
                reasons.append(f"MACD 데드크로스 (hist={macd_hist:+.1f})")

        # ── 4. Regime 보정 ────────────────────────────────────────
        if regime == "Bullish" and action == "BUY":
            score += 0.05
            confidence += 0.05
        elif regime == "Bearish" and action == "SELL":
            score -= 0.05
            confidence += 0.05

        # ── 정규화 ────────────────────────────────────────────────
        final_score = max(0.0, min(1.0, score))
        final_confidence = max(0.30, min(0.90, confidence))

        return StrategyResult(
            name=self.name,
            action=action,
            score=final_score,
            confidence=final_confidence,
            reasons=reasons[:4],
            metadata={
                "ema5": ema5,
                "ema20": ema20,
                "ema60": ema60,
                "rsi": rsi,
                "macd": macd,
                "macd_signal": macd_signal,
                "regime": regime,
            },
        )
