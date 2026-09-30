# -*- coding: utf-8 -*-
"""tests/unit/test_strategy_backtest.py - 프로덕션 앙상블 백테스트 어댑터 검증.

검증 항목:
    TestIndicators       : MACD / Bollinger / Stochastic / volume_ratio / rolling 고저
    TestComputeIndicators: 워밍업 readiness, regime 판정
    TestEnsembleEvaluator: 신호 생성(엣지 트리거), 시뮬레이션
    TestRunnerEnsemble   : BacktestRunner(signal_mode="ensemble") 통합, 입력 검증
"""

import math
from datetime import date, timedelta
from typing import Any, Dict, List

import pytest

from validation.backtest_runner import BacktestRunner
from validation.strategy_backtest import (
    WARMUP_BARS,
    EnsembleEvaluator,
    bollinger,
    compute_indicators,
    macd,
    rolling_high,
    rolling_low,
    stochastic,
    volume_ratio,
)


def _bars(closes: List[float], volumes: List[float] | None = None) -> List[Dict[str, Any]]:
    base = date(2026, 1, 1)
    out: List[Dict[str, Any]] = []
    for i, c in enumerate(closes):
        out.append(
            {
                "date": (base + timedelta(days=i)).isoformat(),
                "open": c,
                "high": c * 1.01,
                "low": c * 0.99,
                "close": c,
                "volume": (volumes[i] if volumes else 1000.0),
            }
        )
    return out


def _uptrend(n: int = 160) -> List[float]:
    return [100.0 + i * 0.7 for i in range(n)]


class _FakeDB:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def get_ohlcv_range(self, ticker: str, start: str, end: str) -> List[Dict[str, Any]]:
        return list(self.rows)


class TestIndicators:
    def test_macd_positive_in_uptrend(self) -> None:
        line, sig, hist = macd(_uptrend(120))
        assert line[0] is None                       # 워밍업
        assert line[-1] is not None and line[-1] > 0
        assert sig[-1] is not None
        assert hist[-1] == pytest.approx(line[-1] - sig[-1])

    def test_bollinger_ordering(self) -> None:
        closes = [100.0 + 5.0 * math.sin(i / 4.0) for i in range(80)]
        up, mid, low = bollinger(closes, 20, 2.0)
        assert up[0] is None
        assert up[-1] > mid[-1] > low[-1]

    def test_bollinger_constant_series(self) -> None:
        closes = [100.0] * 40
        up, mid, low = bollinger(closes, 20, 2.0)
        assert up[-1] == pytest.approx(mid[-1]) == pytest.approx(low[-1])

    def test_stochastic_bounds(self) -> None:
        closes = _uptrend(80)
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]
        k, d = stochastic(highs, lows, closes, 14, 3)
        assert k[10] is None                          # 워밍업
        vals = [v for v in k if v is not None]
        assert vals and all(0.0 <= v <= 100.0 for v in vals)
        assert k[-1] > 70.0                           # 상승 추세 → 상단

    def test_volume_ratio(self) -> None:
        volumes = [1000.0] * 30
        volumes[-1] = 2000.0
        out = volume_ratio(volumes, 20)
        assert out[-1] > 1.5
        assert out[0] is None

    def test_rolling_high_low(self) -> None:
        values = [1.0, 5.0, 3.0, 2.0, 4.0]
        # 워밍업 구간은 존재하는 값만 사용
        assert rolling_high(values, 3) == [1.0, 5.0, 5.0, 5.0, 4.0]
        assert rolling_low(values, 3) == [1.0, 1.0, 1.0, 2.0, 2.0]


class TestComputeIndicators:
    def test_warmup_then_ready(self) -> None:
        inds = compute_indicators(_bars(_uptrend(160)))
        assert inds[0].ready is False
        assert inds[WARMUP_BARS - 2].ready is False   # ema60 미확보
        assert inds[WARMUP_BARS - 1].ready is True    # 60번째 봉부터 확보
        assert inds[-1].tech_data["ema5"] > 0

    def test_regime_bullish_in_uptrend(self) -> None:
        inds = compute_indicators(_bars(_uptrend(160)))
        assert inds[-1].regime == "Bullish"

    def test_regime_bearish_in_downtrend(self) -> None:
        closes = [300.0 - i * 0.7 for i in range(160)]
        inds = compute_indicators(_bars(closes))
        assert inds[-1].regime == "Bearish"


class TestEnsembleEvaluator:
    async def test_entry_indices_in_uptrend(self) -> None:
        evaluator = EnsembleEvaluator()
        bars = _bars(_uptrend(160))
        entries = await evaluator.entry_indices(bars)
        assert isinstance(entries, list)
        assert entries, "강한 상승 추세에서는 앙상블 진입 신호가 1개 이상이어야 함"
        # ema60은 60번째 봉(index=59)부터 확보된다
        assert all(i >= WARMUP_BARS - 1 for i in entries)

    async def test_entries_are_edge_triggered(self) -> None:
        evaluator = EnsembleEvaluator()
        bars = _bars(_uptrend(160))
        actions = await evaluator.signal_actions(bars)
        entries = await evaluator.entry_indices(bars)
        prev = "HOLD"
        expected = []
        for i, a in enumerate(actions):
            if a == "BUY" and prev != "BUY":
                expected.append(i)
            prev = a
        assert entries == expected

    async def test_simulate_returns_trades(self) -> None:
        evaluator = EnsembleEvaluator()
        bars = _bars(_uptrend(160))
        trades = await evaluator.simulate(bars, ticker="005930")
        assert trades
        assert all(t.ticker == "005930" for t in trades)
        assert all(t.entry_price > 0 for t in trades)

    async def test_no_entries_on_flat_series(self) -> None:
        evaluator = EnsembleEvaluator()
        bars = _bars([100.0] * 160)
        assert await evaluator.entry_indices(bars) == []


class TestRunnerEnsemble:
    def test_invalid_signal_mode(self) -> None:
        with pytest.raises(ValueError):
            BacktestRunner(signal_mode="bogus")

    async def test_run_ensemble_end_to_end(self) -> None:
        db = _FakeDB(_bars(_uptrend(240)))
        runner = BacktestRunner(db=db, signal_mode="ensemble", train_ratio=0.7, min_periods=30)
        report = await runner.run("005930", "2026-01-01", "2026-12-31")

        assert report.strategy == "ensemble"
        assert report.bar_count == 240
        assert report.aggregated is not None
        assert len(report.aggregated.fold_results) >= 1
        assert report.trade_count > 0
        assert "ensemble" in report.summary_text()

    async def test_run_ma_cross_still_default(self) -> None:
        db = _FakeDB(_bars([100.0 + 10.0 * math.sin(i / 5.0) for i in range(220)]))
        runner = BacktestRunner(db=db)
        report = await runner.run("005930", "2026-01-01", "2026-12-31")
        assert report.strategy == "ma_cross"
