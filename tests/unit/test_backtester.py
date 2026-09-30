# -*- coding: utf-8 -*-
"""
tests/unit/test_backtester.py - validation/backtester.py 실구현 검증

배경:
    backtester는 418줄 구현(WalkForwardEngine + 지표)이 존재하나
    배선도 테스트도 전무했던 고아 모듈이었다.
    본 테스트는 지표 수학 / Walk-Forward 분할 / 집계가 실제로 정확한지
    결정론적 입력으로 검증한다.

검증 항목:
    TestMathHelpers      : mean/std/sharpe/sortino/MDD/PF/Calmar/MAR
    TestBacktestResult   : 단일 폴드 지표 산출
    TestWalkForwardEngine: rolling/anchored 분할, 실행, 입력 검증
    TestAggregated       : 폴드 집계
    TestBacktesterFacade : 통합 인터페이스(run_simple/run_walk_forward/report)
"""

import math
from datetime import date, timedelta

import pytest

from validation.backtester import (
    _METRIC_CAP,
    AggregatedResult,
    BacktestResult,
    Backtester,
    Trade,
    WalkForwardEngine,
    _build_equity_curve,
    _calmar,
    _mar,
    _max_drawdown,
    _mean,
    _profit_factor,
    _sharpe,
    _sortino,
    _std,
)


def _dates(n: int) -> list[str]:
    """ISO 날짜 문자열 n개 생성 (BacktestResult.compute가 fromisoformat 사용)."""
    base = date(2026, 1, 1)
    return [(base + timedelta(days=i)).isoformat() for i in range(n)]


def _t(return_pct: float) -> Trade:
    """지정 수익률을 가진 더미 Trade 생성."""
    return Trade(
        ticker="005930",
        entry_date="2026-01-02",
        exit_date="2026-01-05",
        entry_price=10000.0,
        exit_price=10000.0 * (1 + return_pct),
        action="BUY",
        return_pct=return_pct,
    )


class TestMathHelpers:
    def test_mean(self) -> None:
        assert _mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)
        assert _mean([]) == 0.0

    def test_std(self) -> None:
        assert _std([1.0, 1.0, 1.0]) == 0.0
        assert _std([1.0]) == 0.0  # n <= ddof
        assert _std([1.0, 3.0]) == pytest.approx(math.sqrt(2.0))

    def test_sharpe_exact(self) -> None:
        returns = [0.01, 0.02, 0.03]
        # mean=0.02, std(ddof=1)=0.01 → 0.02/0.01*sqrt(252)
        assert _sharpe(returns) == pytest.approx(2.0 * math.sqrt(252), rel=1e-9)

    def test_sharpe_edges(self) -> None:
        assert _sharpe([]) == 0.0
        assert _sharpe([0.01]) == 0.0
        assert _sharpe([0.01, 0.01, 0.01]) == 0.0  # std=0 → 0.0 (0-나눔 방어)

    def test_sharpe_is_capped(self) -> None:
        # 표본 2건 + 극소 표준편차 → 비율 폭발 → _METRIC_CAP으로 클램프
        assert _sharpe([9.9e-7, 1.01e-6]) == pytest.approx(_METRIC_CAP)
        # 표준편차가 사실상 0이면 0.0 (0-나눔 방어)
        assert _sharpe([0.5, 0.499999999]) == 0.0

    def test_sortino(self) -> None:
        assert _sortino([0.01, 0.02, 0.03]) == _METRIC_CAP  # 하방 없음 + 양수
        assert _sortino([0.01, -0.01]) == 0.0  # 하방 1개 → ddof=1 std=0
        assert _sortino([]) == 0.0

    def test_max_drawdown(self) -> None:
        assert _max_drawdown([100.0, 120.0, 90.0, 110.0]) == pytest.approx(0.25)
        assert _max_drawdown([100.0, 110.0]) == 0.0
        assert _max_drawdown([]) == 0.0

    def test_build_equity_curve(self) -> None:
        assert _build_equity_curve([0.1, -0.1], 100.0) == pytest.approx([100.0, 110.0, 99.0])

    def test_profit_factor(self) -> None:
        assert _profit_factor([0.1, -0.05, 0.2]) == pytest.approx(6.0)  # 0.3/0.05
        assert _profit_factor([0.1, 0.2]) == _METRIC_CAP  # 손실 없음 → 캡핑
        assert _profit_factor([-0.1]) == 0.0
        assert _profit_factor([]) == 0.0

    def test_calmar_and_mar(self) -> None:
        assert _calmar(0.1, 0.05, 252) == pytest.approx(2.0)
        assert _calmar(0.1, 0.0, 252) == _METRIC_CAP
        assert _calmar(0.1, 0.05, 0) == 0.0
        assert _mar(0.2, 0.1) == pytest.approx(2.0)


class TestBacktestResult:
    def test_compute_known_trades(self) -> None:
        trades = [_t(0.10), _t(-0.05), _t(0.02), _t(-0.01)]
        r = BacktestResult(
            fold_id=0, start_date="2026-01-02", end_date="2026-01-31", trades=trades
        ).compute()
        assert r.total_trades == 4
        assert r.win_count == 2
        assert r.win_rate == pytest.approx(0.5)
        assert r.total_return == pytest.approx(0.06)
        assert r.avg_return == pytest.approx(0.015)
        assert r.max_drawdown > 0.0

    def test_compute_empty_is_noop(self) -> None:
        r = BacktestResult(
            fold_id=0, start_date="2026-01-02", end_date="2026-01-31"
        ).compute()
        assert r.total_trades == 0
        assert r.win_rate == 0.0

    def test_to_dict(self) -> None:
        r = BacktestResult(
            fold_id=1, start_date="2026-01-02", end_date="2026-01-31", trades=[_t(0.1)]
        ).compute()
        d = r.to_dict()
        assert d["fold_id"] == 1
        assert d["total_trades"] == 1


class TestWalkForwardEngine:
    def test_split_rolling(self) -> None:
        dates = _dates(100)
        eng = WalkForwardEngine(train_ratio=0.7, min_periods=30)
        folds = eng._split_periods(dates, "rolling")
        assert len(folds) == 3
        assert [len(tr) for tr, _te in folds] == [70, 70, 70]
        assert [len(te) for _tr, te in folds] == [10, 10, 10]
        # rolling: 훈련 시작점이 10칸씩 이동
        assert [tr[0] for tr, _te in folds] == [dates[0], dates[10], dates[20]]

    def test_split_anchored(self) -> None:
        dates = _dates(100)
        eng = WalkForwardEngine(train_ratio=0.7, min_periods=30)
        folds = eng._split_periods(dates, "anchored")
        # anchored: 훈련 시작점 고정, 끝점만 확장
        assert [tr[0] for tr, _te in folds] == [dates[0], dates[0], dates[0]]
        assert [len(tr) for tr, _te in folds] == [70, 80, 90]

    def test_run_with_strategy(self) -> None:
        dates = _dates(100)
        eng = WalkForwardEngine(train_ratio=0.7, min_periods=30)
        results = eng.run("005930", dates, lambda _tr, _te: [_t(0.01)], mode="rolling")
        assert len(results) == 3
        assert all(r.total_trades == 1 for r in results)
        assert results[0].start_date == dates[70]

    def test_run_invalid_mode(self) -> None:
        eng = WalkForwardEngine()
        with pytest.raises(ValueError):
            eng.run("005930", ["d0", "d1"], lambda _tr, _te: [], mode="bogus")

    def test_run_insufficient_data(self) -> None:
        eng = WalkForwardEngine(train_ratio=0.7, min_periods=30)
        assert eng.run("005930", [f"d{i}" for i in range(10)], lambda _tr, _te: []) == []

    def test_invalid_constructor(self) -> None:
        with pytest.raises(ValueError):
            WalkForwardEngine(train_ratio=1.5)
        with pytest.raises(ValueError):
            WalkForwardEngine(min_periods=1)


class TestAggregated:
    def test_aggregate(self) -> None:
        dates = _dates(100)
        eng = WalkForwardEngine(train_ratio=0.7, min_periods=30)
        results = eng.run("005930", dates, lambda _tr, _te: [_t(0.01), _t(-0.005)])
        agg = eng.aggregate_results(results)
        assert agg.mean_win_rate == pytest.approx(0.5)
        # 3폴드 × (이익 0.01, 손실 0.005) → PF = 0.03 / 0.015 = 2.0
        assert agg.profit_factor == pytest.approx(2.0)
        assert len(agg.to_dict()["folds"]) == 3
        assert "평균 Sharpe" in agg.summary_text()

    def test_empty_aggregate(self) -> None:
        agg = AggregatedResult(fold_results=[]).compute()
        assert agg.consistency_score == 0.0


class TestBacktesterFacade:
    def test_run_simple_and_report(self) -> None:
        bt = Backtester()
        r = bt.run_simple([_t(0.1), _t(-0.05)], "2026-01-02", "2026-01-31")
        assert r.total_trades == 2
        assert "승률" in bt.generate_report(r)

    def test_run_walk_forward(self) -> None:
        bt = Backtester(train_ratio=0.7, min_periods=30)
        dates = _dates(100)
        agg = bt.run_walk_forward("005930", dates, lambda _tr, _te: [_t(0.02)])
        assert len(agg.fold_results) == 3
