"""HybridDecider 컨텍스트 규격 회귀 테스트 (2026-10-07 2차 사고).

사고: deep_analyzer가 dict를 넘겼으나 decide()는 DecisionContext 객체를 요구
→ 'dict' object has no attribute 'signals' → 밤새 33,534건 실패.
"""
import asyncio
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from decision.hybrid_decider import (  # noqa: E402
    DecisionContext,
    DecisionOutput,
    HybridDecider,
    SignalMetrics,
    TimingMetrics,
)


def _ctx(action="BUY", conf=0.75, score=0.7, positions=0, risk=0.0):
    return DecisionContext(
        ticker="005930",
        current_price=70000.0,
        signals=SignalMetrics(action=action, score=score, confidence=conf, sqi=score, consensus=1.0),
        timing=TimingMetrics(
            entry_readiness=score * 100, exit_readiness=(1 - score) * 100,
            entry_recommendation="BUY", exit_recommendation="SELL", best_time="즉시",
        ),
        portfolio_value=0.0,
        existing_positions=positions,
        portfolio_risk=risk,
    )


def test_call_site_uses_context_object():
    src = (ROOT / "scanner" / "deep_analyzer.py").read_text(encoding="utf-8")
    assert "DecisionContext(" in src
    assert "await self.decider.decide(\n                    {" not in src  # dict 전달 금지


def test_decide_returns_output_object():
    out = asyncio.run(HybridDecider().decide(_ctx()))
    assert isinstance(out, DecisionOutput)
    assert hasattr(out, "warnings")


def test_strong_buy_signal():
    out = asyncio.run(HybridDecider().decide(_ctx(action="BUY", conf=0.75, score=0.72)))
    assert out.action.value == "BUY"
    assert out.confidence == pytest.approx(0.75)


def test_weak_signal_holds():
    out = asyncio.run(HybridDecider().decide(_ctx(action="HOLD", conf=0.45, score=0.5)))
    assert out.action.value == "HOLD"


def test_sell_requires_existing_position():
    """Phase 1(무포지션)에서는 SELL 신호가 HOLD로 강등된다(설계)."""
    no_pos = asyncio.run(HybridDecider().decide(_ctx(action="SELL", conf=0.7, score=0.35, positions=0)))
    with_pos = asyncio.run(HybridDecider().decide(_ctx(action="SELL", conf=0.7, score=0.35, positions=1)))
    assert no_pos.action.value == "HOLD"
    assert with_pos.action.value == "SELL"


def test_high_portfolio_risk_blocks_buy():
    out = asyncio.run(HybridDecider().decide(_ctx(action="BUY", conf=0.75, score=0.72, risk=90.0)))
    assert out.action.value == "HOLD"


def test_decide_requires_context_not_dict():
    sig = inspect.signature(HybridDecider.decide)
    ann = sig.parameters["context"].annotation
    assert ann is DecisionContext
