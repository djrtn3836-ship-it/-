# -*- coding: utf-8 -*-
"""공시 전략 백테스트(S145) 테스트 — 회계·비용·지표·중복."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from validation.disclosure_strategy_test import (  # noqa: E402
    build_signals,
    metrics,
    simulate,
    trade_stats,
    yearly_returns,
)


def days(n=12, start="2022-01-03"):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def series_linear(n=12):
    ds = days(n)
    closes = [100.0 + i for i in range(n)]
    return ds, [c - 0.5 for c in closes], closes, [1000.0] * n


class TestSimulate:

    def test_position_survives_days(self):
        ds, opens, closes, vols = series_linear()
        series = {"A": (ds, opens, closes, vols)}
        sig = [{"ticker": "A", "category": "자사주매입", "entry_date": ds[2],
                "entry_price": closes[2], "exit_date": ds[4], "exit_idx": 4, "hold": 2}]
        sim = simulate(sig, series, ds, max_positions=1, cost=0.0015)
        eq = sim["equity"]
        assert eq[1] == 1.0, "진입 전 현금"
        assert 0.98 < eq[2] < 1.0, "매수 비용 반영"
        assert eq[3] > eq[2], "보유 중 평가액 유지(회귀: 소멸 버그)"
        assert eq[4] > 1.0, "청산 후 이익"
        assert eq[4] == eq[5], "청산 후 현금 고정"

    def test_net_of_costs(self):
        ds, opens, closes, vols = series_linear()
        series = {"A": (ds, opens, closes, vols)}
        sig = [{"ticker": "A", "category": "무상증자", "entry_date": ds[2],
                "entry_price": closes[2], "exit_date": ds[4], "exit_idx": 4, "hold": 2}]
        eq = simulate(sig, series, ds, max_positions=1, cost=0.0015)["equity"]
        expected = (closes[4] / closes[2]) * (1 / 1.0015) * 0.9985
        assert abs(eq[4] - expected) < 1e-6

    def test_max_positions(self):
        ds, opens, closes, vols = series_linear()
        series = {t: (ds, opens, closes, vols) for t in ("A", "B", "C")}
        sig = [
            {"ticker": t, "category": "자사주매입", "entry_date": ds[2],
             "entry_price": closes[2], "exit_date": ds[6], "exit_idx": 6, "hold": 4}
            for t in ("A", "B", "C")
        ]
        sim = simulate(sig, series, ds, max_positions=2, cost=0.0)
        assert sim["equity"][3] > 1.0, "2종목만 보유(현금 일부)"


class TestSignals:

    def test_dedupe_and_hold(self):
        ds, opens, closes, vols = series_linear(40)
        series = {"A": (ds, opens, closes, vols)}
        tickers = {"A": {"events": [
            {"date": "20220105", "title": "주요사항보고서(자기주식취득결정)", "rcept_no": "1"},
            {"date": "20220107", "title": "주요사항보고서(자기주식취득결정)", "rcept_no": "2"},
            {"date": "20220110", "title": "주요사항보고서(무상증자결정)", "rcept_no": "3"},
        ]}}
        sigs = build_signals(tickers, series)
        assert len(sigs) == 2, "20거래일 내 같은 유형 중복 제거"
        assert {s["category"]: s["hold"] for s in sigs} == {"자사주매입": 5, "무상증자": 5}

    def test_open_entry(self):
        ds, opens, closes, vols = series_linear(40)
        series = {"A": (ds, opens, closes, vols)}
        tickers = {"A": {"events": [{"date": "20220105", "title": "합병결정", "rcept_no": "1"}]}}
        sigs = build_signals(tickers, series, entry="open")
        assert len(sigs) == 1 and sigs[0]["entry_price"] == opens[3]


class TestMetrics:

    def test_mdd_and_total(self):
        m = metrics([1.0, 1.1, 1.0], days(3))
        assert m["mdd"] == 9.1 and m["total"] == 0.0

    def test_yearly(self):
        ds = ["2022-12-30", "2023-01-02", "2023-01-03", "2024-01-02"]
        y = yearly_returns([1.0, 1.1, 1.2, 1.3], ds)
        assert y["2022"] == 0.0 and y["2023"] == 20.0 and y["2024"] == 8.3

    def test_trade_stats_cost(self):
        ds, opens, closes, vols = series_linear()
        series = {"A": (ds, opens, closes, vols)}
        sig = [{"ticker": "A", "entry_price": closes[2], "exit_date": ds[4]}]
        st = trade_stats(sig, series, cost=0.0015)
        assert st["n"] == 1 and abs(st["avg_net"] - ((closes[4] / closes[2] - 1) - 0.003) * 100) < 0.01
