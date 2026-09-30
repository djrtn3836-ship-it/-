# -*- coding: utf-8 -*-
"""tests/unit/test_momentum_backtest.py - 횡단면 모멘텀 백테스터 검증 (오프라인)."""

import math
from typing import Dict, List, Sequence

import pytest

from validation.momentum_backtest import (
    MomentumConfig,
    build_price_panel,
    compute_metrics,
    momentum_returns,
    select_top_k,
    sweep_momentum,
)


class TestSelect:
    def test_orders_desc(self) -> None:
        mom = {"A": 0.1, "B": 0.3, "C": -0.05}
        assert select_top_k(mom, 2) == ["B", "A"]
        assert select_top_k(mom, 10) == ["B", "A", "C"]

    def test_empty(self) -> None:
        assert select_top_k({}, 3) == []


class TestPanel:
    def test_aligns_dates(self) -> None:
        series = {"A": {"d0": 10.0, "d1": 11.0}, "B": {"d0": 20.0}}
        dates = ["d0", "d1"]
        panel = build_price_panel(series, dates)
        assert panel[0] == {"A": 10.0, "B": 20.0}
        assert panel[1] == {"A": 11.0}   # B는 d1 누락

    def test_filters_nonpositive_prices(self) -> None:
        panel = build_price_panel({"A": {"d0": 0.0, "d1": 5.0}}, ["d0", "d1"])
        assert panel[0] == {}
        assert panel[1] == {"A": 5.0}


class TestMomentumReturns:
    def _dates(self, n: int) -> List[str]:
        return [f"d{i:03d}" for i in range(n)]

    def test_selects_winner(self) -> None:
        # A=강한 상승, B=횡보, C=하락. lookback=2, hold=1, top_k=1 → A 선택
        dates = self._dates(6)
        series = {
            "A": {d: 100.0 + 10.0 * i for i, d in enumerate(dates)},
            "B": {d: 100.0 for d in dates},
            "C": {d: 100.0 - 5.0 * i for i, d in enumerate(dates)},
        }
        panel = build_price_panel(series, dates)
        cfg = MomentumConfig(lookback=2, top_k=1, hold_days=1)

        returns = momentum_returns(dates, panel, cfg)

        assert returns, "수익률 시계열이 생성되어야 함"
        # 첫 리밸런싱(i=2): A 모멘텀 = 120/100-1 = 0.2 로 1위 → 다음날 130/120-1
        assert returns[0] == pytest.approx(130.0 / 120.0 - 1.0)

    def test_equal_weight_basket(self) -> None:
        dates = self._dates(5)
        series = {
            "A": {d: 100.0 + 10.0 * i for i, d in enumerate(dates)},
            "B": {d: 100.0 + 8.0 * i for i, d in enumerate(dates)},
            "C": {d: 100.0 for d in dates},
        }
        panel = build_price_panel(series, dates)
        cfg = MomentumConfig(lookback=1, top_k=2, hold_days=1)

        returns = momentum_returns(dates, panel, cfg)

        # i=1: A모멘텀=0.1, B=0.08, C=0 → 상위 2 = A,B → 평균(110→120, 108→116)
        expected = ((120.0 / 110.0 - 1.0) + (116.0 / 108.0 - 1.0)) / 2
        assert returns[0] == pytest.approx(expected)

    def test_insufficient_dates(self) -> None:
        dates = self._dates(3)
        series = {"A": {d: 100.0 for d in dates}}
        panel = build_price_panel(series, dates)
        assert momentum_returns(dates, panel, MomentumConfig(lookback=60, top_k=1, hold_days=20)) == []


class TestMetrics:
    def test_known_returns(self) -> None:
        m = compute_metrics([0.10, -0.05, 0.10], hold_days=1)
        assert m.periods == 3
        assert m.total_return == pytest.approx(0.1495, rel=1e-6)
        assert m.win_rate == pytest.approx(2 / 3)
        assert m.max_drawdown == pytest.approx(0.05, rel=1e-6)
        assert m.sharpe > 0

    def test_empty(self) -> None:
        m = compute_metrics([], hold_days=1)
        assert m.periods == 0
        assert m.total_return == 0.0
        assert m.sharpe == 0.0

    def test_all_losses(self) -> None:
        # 표준편차가 0이면 Sharpe는 정의되지 않으므로 0.0 (0-나눔 방어)
        assert compute_metrics([-0.1, -0.1, -0.1], hold_days=1).sharpe == 0.0
        # 변동이 있는 손실 구간은 음의 Sharpe
        mixed = compute_metrics([-0.10, -0.05, -0.08], hold_days=1)
        assert mixed.total_return < 0
        assert mixed.win_rate == 0.0
        assert mixed.sharpe < 0

    def test_sharpe_scales_with_hold(self) -> None:
        rets = [0.02, -0.01, 0.03, 0.01]
        s1 = compute_metrics(rets, hold_days=1).sharpe
        s20 = compute_metrics(rets, hold_days=20).sharpe
        assert s1 > s20 > 0   # 보유기간이 길수록 연환산 계수 감소


class TestSweep:
    def test_sorted_by_sharpe(self) -> None:
        dates = [f"d{i:03d}" for i in range(200)]
        series = {
            "A": {d: 100.0 * math.exp(0.002 * i) for i, d in enumerate(dates)},
            "B": {d: 100.0 for d in dates},
            "C": {d: 100.0 * math.exp(-0.001 * i) for i, d in enumerate(dates)},
        }
        panel = build_price_panel(series, dates)

        rows = sweep_momentum(dates, panel, lookbacks=(5, 20), top_ks=(1, 2), holds=(5, 10))

        assert len(rows) == 8
        sharpes = [r.is_metrics.sharpe for r in rows]
        assert sharpes == sorted(sharpes, reverse=True)
        assert "lb=" in rows[0].label()


class TestSurvivorshipStress:
    """P2-13: 생존편향(상장폐지 누락) 스트레스 검증."""

    @staticmethod
    def _flat_panel(n: int = 30) -> tuple:
        dates = [f"2025-01-{i:02d}" for i in range(1, n + 1)]
        panel = [{"A": 100.0, "B": 100.0} for _ in dates]
        return dates, panel

    def test_delist_drag_lowers_returns_by_expected_amount(self) -> None:
        dates, panel = self._flat_panel()
        base = MomentumConfig(lookback=5, top_k=1, hold_days=5, cost_pct=0.0)
        stressed = MomentumConfig(
            lookback=5, top_k=1, hold_days=5, cost_pct=0.0,
            delist_rate_annual=0.05, delist_loss=0.6,
        )
        r_base = momentum_returns(dates, panel, base)
        r_stress = momentum_returns(dates, panel, stressed)

        assert r_base and len(r_base) == len(r_stress)
        expected_drag = 0.05 * (5 / 252) * 0.6
        for a, b in zip(r_base, r_stress):
            assert abs((a - b) - expected_drag) < 1e-12

    def test_zero_rate_keeps_returns_unchanged(self) -> None:
        dates, panel = self._flat_panel()
        cfg = MomentumConfig(lookback=5, top_k=1, hold_days=5, cost_pct=0.0,
                             delist_rate_annual=0.0)
        assert all(abs(r) < 1e-12 for r in momentum_returns(dates, panel, cfg))

    def test_missing_future_price_is_counted_as_delist_loss(self) -> None:
        dates, panel = self._flat_panel()
        panel[10] = {}  # 보유기간 중 A 가격 소멸(상장폐지)
        cfg = MomentumConfig(lookback=5, top_k=1, hold_days=5, cost_pct=0.0,
                             delist_loss=0.6)
        returns = momentum_returns(dates, panel, cfg)

        assert returns[0] == pytest.approx(-0.6)

    def test_missing_price_uses_configured_loss(self) -> None:
        dates, panel = self._flat_panel()
        panel[10] = {}
        cfg = MomentumConfig(lookback=5, top_k=1, hold_days=5, cost_pct=0.0,
                             delist_loss=0.9)
        assert momentum_returns(dates, panel, cfg)[0] == pytest.approx(-0.9)
