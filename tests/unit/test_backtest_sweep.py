# -*- coding: utf-8 -*-
"""tests/unit/test_backtest_sweep.py - 다종목 일괄 백테스트 / 파라미터 스윕 검증 (오프라인).

검증 항목:
    TestTickerSeries  : date_to_idx 자동 생성, dates
    TestBatchMetrics  : 집계 dict/summary
    TestEvaluateCombo : 임계값 민감도(거래 유/무), 집계 정확성
    TestRunSweep      : 정렬(mean_sharpe 내림차순), 조합 수
    TestPrepareSeries : 유효 종목 필터링(min_bars), FakeDB 연동
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import pytest

from validation.backtest_runner import SimConfig
from validation.backtest_sweep import (
    BatchMetrics,
    OOSRow,
    SweepRow,
    TickerSeries,
    evaluate_combo,
    prepare_series,
    run_oos_check,
    run_rolling_oos,
    run_sweep,
    slice_series,
)


def _bars(closes: List[float]) -> List[Dict[str, Any]]:
    base = date(2026, 1, 1)
    return [
        {
            "date": (base + timedelta(days=i)).isoformat(),
            "open": c,
            "high": c * 1.02,
            "low": c * 0.98,
            "close": c,
            "volume": 1000.0,
        }
        for i, c in enumerate(closes)
    ]


def _series(ticker: str, n: int = 240, buy_score: float = 0.30, warmup: int = 60) -> TickerSeries:
    closes = [100.0 + i * 0.5 for i in range(n)]
    scores: List[Optional[float]] = [None] * min(warmup, n) + [buy_score] * max(0, n - warmup)
    return TickerSeries(ticker=ticker, bars=_bars(closes), net_scores=scores)


class _FakeDB:
    def __init__(self, data: Dict[str, List[Dict[str, Any]]]) -> None:
        self.data = data

    async def get_ohlcv_range(self, ticker: str, start: str, end: str) -> List[Dict[str, Any]]:
        return list(self.data.get(ticker, []))


class TestTickerSeries:
    def test_date_index_autobuilt(self) -> None:
        ts = _series("005930", n=70)
        assert len(ts.date_to_idx) == 70
        assert ts.dates[0] == ts.bars[0]["date"]
        assert ts.date_to_idx[ts.dates[0]] == 0

    def test_none_scores_before_warmup(self) -> None:
        ts = _series("005930", n=80, warmup=60)
        assert ts.net_scores[0] is None
        assert ts.net_scores[59] is None
        assert ts.net_scores[60] == 0.30


class TestBatchMetrics:
    def test_defaults_and_dict(self) -> None:
        m = BatchMetrics(tickers=3, evaluated=2, total_trades=7, mean_sharpe=1.2345)
        d = m.to_dict()
        assert d["tickers"] == 3
        assert d["mean_sharpe"] == 1.2345
        assert "평가 2/3종목" in m.summary()


class TestEvaluateCombo:
    def test_threshold_sensitivity(self) -> None:
        series = [_series("005930", buy_score=0.30)]
        sim = SimConfig()

        low = evaluate_combo(series, threshold=0.25, sim_config=sim)
        high = evaluate_combo(series, threshold=0.35, sim_config=sim)

        assert low.evaluated == 1
        assert low.total_trades > 0
        assert high.total_trades == 0        # 임계값 미달 → 신호 없음

    def test_aggregates_across_tickers(self) -> None:
        series = [_series("005930"), _series("000660"), _series("035420")]
        metrics = evaluate_combo(series, threshold=0.25, sim_config=SimConfig())
        assert metrics.tickers == 3
        assert metrics.evaluated == 3
        assert metrics.total_trades > 0
        assert -1.0 <= metrics.profitable_pct <= 1.0

    def test_empty_series_is_safe(self) -> None:
        metrics = evaluate_combo([], threshold=0.25, sim_config=SimConfig())
        assert metrics.evaluated == 0
        assert metrics.total_trades == 0
        assert metrics.mean_sharpe == 0.0


class TestRunSweep:
    def test_rows_sorted_and_counted(self) -> None:
        series = [_series("005930"), _series("000660")]
        thresholds = (0.25, 0.35)
        sims = (SimConfig(hold_days=3), SimConfig(hold_days=10))

        rows = run_sweep(series, thresholds, sims)

        assert len(rows) == len(thresholds) * len(sims)
        # 순위: 중앙 Sharpe 우선(외란 강건), 평균·거래 수는 보조
        keys = [(r.metrics.median_sharpe, r.metrics.mean_sharpe, r.metrics.total_trades) for r in rows]
        assert keys == sorted(keys, reverse=True)
        assert all(isinstance(r, SweepRow) for r in rows)
        assert "thr=" in rows[0].label()


class TestSliceSeries:
    def test_slices_bars_and_scores_together(self) -> None:
        ts = _series("005930", n=240)
        sliced = slice_series(ts, ts.dates[60], ts.dates[179], min_bars=90)
        assert sliced is not None
        assert len(sliced.bars) == 120
        assert len(sliced.net_scores) == 120
        # net score가 봉과 동일하게 잘렸는지(정렬 유지)
        assert sliced.net_scores[0] == ts.net_scores[60]

    def test_returns_none_when_too_short(self) -> None:
        ts = _series("005930", n=240)
        assert slice_series(ts, ts.dates[0], ts.dates[10], min_bars=90) is None


class TestRunOOSCheck:
    def test_returns_oos_rows_with_drop(self) -> None:
        series = [_series("005930", n=400), _series("000660", n=400)]
        split = series[0].dates[239]
        oos_start = series[0].dates[240]

        rows = run_oos_check(
            series,
            is_start=series[0].dates[0],
            split_date=split,
            oos_start=oos_start,
            oos_end=series[0].dates[-1],
            thresholds=(0.20, 0.35),
            sim_configs=(SimConfig(hold_days=5),),
            top_n=2,
        )

        assert len(rows) == 2
        assert all(isinstance(r, OOSRow) for r in rows)
        assert all(r.is_metrics.evaluated >= 1 for r in rows)
        assert all(r.oos_metrics.evaluated >= 1 for r in rows)
        assert rows[0].label().startswith("thr=")
        # median_drop = OOS - IS
        assert rows[0].median_drop() == pytest.approx(
            rows[0].oos_metrics.median_sharpe - rows[0].is_metrics.median_sharpe
        )


class TestRunRollingOOS:
    def test_multiple_splits(self) -> None:
        series = [_series("005930", n=600), _series("000660", n=600)]
        results = run_rolling_oos(
            series,
            n_splits=2,
            thresholds=(0.20, 0.35),
            sim_configs=(SimConfig(hold_days=5),),
        )
        assert len(results) == 2
        for r in results:
            assert r.tuned_oos.evaluated >= 1
            assert r.baseline_oos.evaluated >= 1
            assert isinstance(r.degraded(), bool)
            assert r.is_start < r.is_end < r.oos_start < r.oos_end
        assert results[0].split_index == 1

    def test_insufficient_data_returns_empty(self) -> None:
        series = [_series("005930", n=100)]
        assert run_rolling_oos(series, n_splits=3) == []


class TestPrepareSeries:
    async def test_filters_short_series(self) -> None:
        long_rows = _bars([100.0 + i * 0.5 for i in range(160)])
        short_rows = _bars([100.0 + i * 0.5 for i in range(50)])   # min_bars 미달
        db = _FakeDB({"005930": long_rows, "000660": short_rows})

        series = await prepare_series(db, ["005930", "000660"], "2026-01-01", "2026-12-31", min_bars=120)

        assert [s.ticker for s in series] == ["005930"]
        assert len(series[0].net_scores) == len(series[0].bars)

    async def test_empty_db(self) -> None:
        series = await prepare_series(_FakeDB({}), ["005930"], "2026-01-01", "2026-12-31")
        assert series == []
