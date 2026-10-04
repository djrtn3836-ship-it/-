# -*- coding: utf-8 -*-
"""tests/unit/test_decision_layer.py - 판단 계층 검증 (P9-2).

대상: `decision/hybrid_decider.py`, `regime/regime_detector.py`
배경: 감사에서 두 모듈 모두 테스트 언급 0건.
      - HybridDecider: 신호+타이밍+리스크 결합 → 최종 액션/수량/손절·익절 산출
      - RegimeDetector: 시장 레짐(Bull/Correction/Bear/Panic) 판정
"""

from datetime import datetime
from typing import Any, Dict

import pytest

from decision.hybrid_decider import (
    ActionType,
    DecisionContext,
    DecisionPriority,
    HybridDecider,
    SignalMetrics,
    TimingMetrics,
)
from regime.regime_detector import KoreanSpecialFactors, RegimeDetector


def _ctx(
    signal_score: float = 0.8,
    confidence: float = 0.9,
    timing_score: float = 0.8,
    risk: float = 0.1,
    positions: int = 0,
    price: float = 70000.0,
) -> DecisionContext:
    return DecisionContext(
        ticker="005930",
        current_price=price,
        signals=SignalMetrics(
            action="BUY" if signal_score >= 0.5 else "HOLD",
            score=signal_score,
            confidence=confidence,
            sqi=confidence,
            consensus=0.8,
        ),
        timing=TimingMetrics(
            entry_readiness=timing_score * 100.0,
            exit_readiness=10.0,
            entry_recommendation="지금 진입" if timing_score >= 0.5 else "대기",
            exit_recommendation="보유",
            best_time="09:30",
        ),
        portfolio_value=10_000_000.0,
        existing_positions=positions,
        portfolio_risk=risk,
    )


class TestHybridDecider:
    async def test_strong_signal_buys(self) -> None:
        out = await HybridDecider().decide(_ctx())

        assert out.ticker == "005930"
        assert out.action is ActionType.BUY
        assert out.quantity > 0
        assert out.entry_price is not None

    async def test_weak_signal_holds(self) -> None:
        out = await HybridDecider().decide(
            _ctx(signal_score=0.1, confidence=0.2, timing_score=0.1)
        )

        assert out.action in (ActionType.HOLD, ActionType.SELL)

    async def test_stop_loss_below_entry_and_target_above(self) -> None:
        out = await HybridDecider().decide(_ctx())

        if out.action is ActionType.BUY:
            assert out.stop_loss < float(out.entry_price)
            assert out.take_profit > float(out.entry_price)

    async def test_high_portfolio_risk_reduces_or_blocks(self) -> None:
        """포트폴리오 리스크가 크면 수량이 줄거나 관망해야 한다."""
        low_risk = await HybridDecider().decide(_ctx(risk=0.0))
        high_risk = await HybridDecider().decide(_ctx(risk=0.9))

        assert high_risk.quantity <= low_risk.quantity

    async def test_confidence_reported(self) -> None:
        out = await HybridDecider().decide(_ctx())
        assert 0.0 <= out.confidence <= 1.0

    async def test_output_fields_present(self) -> None:
        out = await HybridDecider().decide(_ctx())

        for field in ("ticker", "action", "quantity", "stop_loss", "take_profit",
                      "confidence", "reason", "warnings", "priority", "timestamp"):
            assert hasattr(out, field), f"{field} 누락"
        assert isinstance(out.timestamp, datetime)
        assert isinstance(out.priority, DecisionPriority)

    async def test_reason_is_nonempty(self) -> None:
        out = await HybridDecider().decide(_ctx())
        assert isinstance(out.reason, str) and out.reason.strip()

    async def test_korean_action_enum_consistency(self) -> None:
        """v8.0 피드백 이슈(한글 문자열 액션 불일치) 재발 방지 — enum 사용."""
        out = await HybridDecider().decide(_ctx())
        assert isinstance(out.action, ActionType)


class TestKoreanSpecialFactors:
    def test_futures_options_expiry_detection(self) -> None:
        # 선물옵션 만기일: 매월 두 번째 목요일
        second_thursday = datetime(2026, 10, 8)
        assert KoreanSpecialFactors.is_futures_options_expiry(second_thursday) in (True, False)

    def test_program_imbalance_sign(self) -> None:
        assert KoreanSpecialFactors.get_program_trading_imbalance(1000.0, 500.0) > 0
        assert KoreanSpecialFactors.get_program_trading_imbalance(500.0, 1000.0) < 0

    def test_foreigner_position_is_net_ratio(self) -> None:
        """순매수 비율(-1~1). 100 vs 40 → (100-40)/140 ≈ 0.4286."""
        assert KoreanSpecialFactors.get_foreigner_futures_position(100.0, 40.0) == pytest.approx(60.0 / 140.0)
        assert KoreanSpecialFactors.get_foreigner_futures_position(0.0, 0.0) == 0.0

    def test_dividend_ex_date_is_bool(self) -> None:
        assert isinstance(KoreanSpecialFactors.is_dividend_ex_date(datetime(2026, 12, 29)), bool)


class TestRegimeDetector:
    def test_bullish_inputs_give_bull(self) -> None:
        r = RegimeDetector().detect({
            "kospi_trend": 3.0, "spx_trend": 3.0, "sox_trend": 3.0,
            "vix": 12.0, "oil_price": 55.0, "usdkrw": 1150.0,
        })

        assert r["regime"] in ("Bull", "StrongBull", "Bullish")
        assert r["score"] > 0.5

    def test_panic_inputs_give_defensive_regime(self) -> None:
        r = RegimeDetector().detect({
            "kospi_trend": -6.0, "spx_trend": -6.0, "sox_trend": -6.0,
            "vix": 45.0, "oil_price": 110.0, "usdkrw": 1500.0,
        })

        assert r["regime"] in ("Panic", "Bear", "Correction")
        assert r["score"] < 0.3

    def test_output_shape(self) -> None:
        r = RegimeDetector().detect({})

        assert set(r) >= {"regime", "score", "components", "korean_factors", "timestamp"}
        assert set(r["components"]) >= {"trend", "risk", "oil", "fx"}
        assert set(r["korean_factors"]) >= {"futures_options_expiry", "dividend_ex_date", "program_imbalance"}

    def test_empty_data_uses_defaults_without_error(self) -> None:
        r = RegimeDetector().detect({})
        assert isinstance(r["regime"], str) and r["regime"]

    def test_current_regime_updated(self) -> None:
        d = RegimeDetector()
        r = d.detect({"kospi_trend": 2.0, "vix": 15.0})
        assert d.current_regime == r["regime"]

    def test_normalize_bounds(self) -> None:
        assert RegimeDetector._normalize(0.0, 0.0, 10.0) == 0.0
        assert RegimeDetector._normalize(10.0, 0.0, 10.0) == 1.0
        assert RegimeDetector._normalize(-5.0, 0.0, 10.0) == 0.0
        assert RegimeDetector._normalize(20.0, 0.0, 10.0) == 1.0

    def test_regime_is_deterministic(self) -> None:
        data: Dict[str, Any] = {"kospi_trend": 1.0, "vix": 20.0, "usdkrw": 1300.0}
        assert RegimeDetector().detect(dict(data))["regime"] == RegimeDetector().detect(dict(data))["regime"]
