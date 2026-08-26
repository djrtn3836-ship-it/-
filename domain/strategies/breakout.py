# -*- coding: utf-8 -*-
"""
domain/strategies/breakout.py - V10 Breakout Strategy v2.0

개선 사항 (v2.0):
    - 볼린저 밴드 Squeeze 감지 (밴드폭이 좁아졌다가 폭발)
    - 거래량 급증 확인 강화 (2.0x/3.0x 차등)
    - 52주 신고가 근접 (5% 이내) 신호 추가
    - MACD 히스토그램 방향 확인
    - Google Style Docstrings 100%
"""

from typing import Any, Dict

from domain.strategies.base import Strategy, StrategyResult


class BreakoutStrategy(Strategy):
    """돌파 전략 (52주 고저 + 볼린저 스퀴즈 + 거래량 급증).

    가격이 주요 저항/지지선을 돌파하는 순간을 포착합니다.
    볼린저 밴드 스퀴즈(squeeze) 후 폭발적 확장을 이용합니다.

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
        return "Breakout"

    @property
    def weight(self) -> float:
        """앙상블 가중치."""
        return self._weight

    async def analyze(self, data: Dict[str, Any]) -> StrategyResult:
        """돌파 분석 실행.

        분석 순서:
            1. EMA 정배열/역배열 (기본 방향)
            2. 52주 고저 돌파 감지
            3. 거래량 급증 확인 (방향성 강화)
            4. 볼린저 밴드 스퀴즈 감지
            5. MACD 히스토그램 방향 확인

        Args:
            data: 시장 데이터 딕셔너리
                - tech_data (dict): EMA5/20/60, volume_ratio, bb_upper/lower/middle
                - price (float): 현재가
                - high_52w (float): 52주 최고가
                - low_52w (float): 52주 최저가

        Returns:
            StrategyResult: 돌파 분석 결과
        """
        tech_data: Dict[str, Any] = data.get("tech_data", {})
        price: float = float(data.get("price", 0))
        high_52w: float = float(data.get("high_52w", price * 1.20))
        low_52w: float = float(data.get("low_52w", price * 0.80))

        ema5: float = tech_data.get("ema5", price)
        ema20: float = tech_data.get("ema20", price)
        ema60: float = tech_data.get("ema60", price)
        volume_ratio: float = tech_data.get("volume_ratio", 1.0)
        bb_upper: float = tech_data.get("bb_upper", price * 1.05)
        bb_lower: float = tech_data.get("bb_lower", price * 0.95)
        bb_middle: float = tech_data.get("bb_middle", price)
        macd_hist: float = tech_data.get("macd_hist", 0.0)

        score: float = 0.5
        confidence: float = 0.5
        action: str = "HOLD"
        reasons = []

        # ── 1. EMA 방향 설정 ─────────────────────────────────────
        if ema5 > 0 and ema20 > 0 and ema60 > 0:
            if ema5 > ema20 > ema60:
                score += 0.22
                confidence += 0.10
                action = "BUY"
                reasons.append("EMA 정배열 상승추세")
            elif ema5 < ema20 < ema60:
                score -= 0.22
                confidence += 0.10
                action = "SELL"
                reasons.append("EMA 역배열 하락추세")
            elif ema5 > ema20:
                score += 0.10
                reasons.append(f"단기 상승 (EMA5>{ema20:.0f})")
            elif ema5 < ema20:
                score -= 0.10
                reasons.append(f"단기 하락 (EMA5<{ema20:.0f})")

        # ── 2. 52주 고저 돌파 ─────────────────────────────────────
        if price > 0 and high_52w > 0:
            ratio_to_high = (high_52w - price) / high_52w

            if price >= high_52w:
                # 52주 신고가 돌파 → 강한 매수
                score += 0.32
                confidence += 0.15
                if action == "HOLD":
                    action = "BUY"
                reasons.append(f"52주 신고가 돌파 ({price:.0f} ≥ {high_52w:.0f})")
            elif ratio_to_high <= 0.05:
                # 신고가 5% 이내 근접 → 돌파 임박
                score += 0.15
                confidence += 0.08
                if action == "HOLD":
                    action = "BUY"
                reasons.append(f"신고가 근접 ({ratio_to_high:.1%} 이내)")

        if price > 0 and low_52w > 0:
            if price <= low_52w:
                # 52주 신저가 이탈 → 강한 매도
                score -= 0.32
                confidence += 0.15
                if action == "HOLD":
                    action = "SELL"
                reasons.append(f"52주 신저가 이탈 ({price:.0f} ≤ {low_52w:.0f})")
            elif (price - low_52w) / low_52w <= 0.05:
                # 신저가 5% 이내 근접 → 이탈 임박
                score -= 0.15
                confidence += 0.08
                if action == "HOLD":
                    action = "SELL"
                reasons.append(f"신저가 근접 ({(price - low_52w) / low_52w:.1%} 이내)")

        # ── 3. 거래량 급증 (방향성 강화) ─────────────────────────
        if volume_ratio >= 3.0:
            delta = 0.18 if action in ("BUY", "HOLD") else -0.18
            score += delta
            confidence += 0.12
            reasons.append(f"거래량 폭발 (×{volume_ratio:.1f})")
        elif volume_ratio >= 2.0:
            delta = 0.10 if action in ("BUY", "HOLD") else -0.10
            score += delta
            confidence += 0.06
            reasons.append(f"거래량 급증 (×{volume_ratio:.1f})")
        elif volume_ratio < 0.5:
            # 거래량 극도 축소 → 신호 약화
            if action == "BUY":
                score -= 0.08
                confidence -= 0.05
            reasons.append(f"거래량 급감 (×{volume_ratio:.1f}) → 신호 약화")

        # ── 4. 볼린저 밴드 스퀴즈 감지 ──────────────────────────
        if bb_upper > 0 and bb_lower > 0 and bb_middle > 0:
            bb_width_pct = (bb_upper - bb_lower) / bb_middle
            if bb_width_pct < 0.04:
                # 밴드폭 4% 미만 → 스퀴즈 진행 중 (에너지 축적)
                confidence += 0.06
                reasons.append(f"볼린저 스퀴즈 감지 (폭={bb_width_pct:.1%})")

        # ── 5. MACD 히스토그램 방향 확인 ────────────────────────
        if macd_hist != 0.0:
            if macd_hist > 0 and action in ("BUY",):
                score += 0.06
                reasons.append("MACD 히스토그램 양전환")
            elif macd_hist < 0 and action in ("SELL",):
                score -= 0.06
                reasons.append("MACD 히스토그램 음전환")

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
                "ema5": ema5,
                "ema20": ema20,
                "ema60": ema60,
                "volume_ratio": volume_ratio,
                "high_52w": high_52w,
                "low_52w": low_52w,
                "macd_hist": macd_hist,
            },
        )
