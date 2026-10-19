# -*- coding: utf-8 -*-
"""신호 vs 무작위 비교 도구(S136 ⑤) 테스트."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analytics.market_breadth import S  # noqa: E402
from validation.signal_vs_random_test import (  # noqa: E402
    fwd,
    load_signals,
    run_horizon,
)


class TestLoad:

    def test_dedupe_and_bad_lines(self, tmp_path):
        p = tmp_path / "s.jsonl"
        p.write_text(
            '{"ticker": "A", "date": "2026-01-02"}\n'
            "not json\n"
            '{"ticker": "A", "date": "2026-01-02"}\n'
            '{"ticker": "B", "date": "2026-01-03"}\n',
            encoding="utf-8",
        )
        rows = load_signals(p)
        assert len(rows) == 2, "(종목, 날짜) 중복 제거 + 깨진 줄 스킵"

    def test_missing_file(self, tmp_path):
        assert load_signals(tmp_path / "none.jsonl") == []


def _series(prices: dict) -> dict:
    out = {}
    n = len(next(iter(prices.values())))
    for t, path in prices.items():
        dates = [f"2026-01-{i + 1:02d}" for i in range(len(path))]
        out[t] = S(dates, [float(c) for c in path], [], [])
    assert n > 0
    return out


class TestFwd:

    def test_bounds(self):
        s = _series({"A": [100, 101, 102]})
        idx = {"A": {d: i for i, d in enumerate(s["A"].dates)}}
        assert fwd(s, idx, "A", "2026-01-02", 1) is not None
        assert fwd(s, idx, "A", "2026-01-02", 5) is None, "미래 부족 → None"

    def test_artifact_excluded(self):
        s = _series({"A": [100, 160, 161]})  # +60% = 분할류
        idx = {"A": {d: i for i, d in enumerate(s["A"].dates)}}
        assert fwd(s, idx, "A", "2026-01-01", 1) is None


class TestRunHorizon:

    def test_signal_outperforms_flat_universe(self):
        prices = {f"t{k}": [100] * 12 for k in range(40)}
        prices["t0"] = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120, 122]
        series = _series(prices)
        idx_map = {t: {d: i for i, d in enumerate(s.dates)} for t, s in series.items()}
        signals = [{"ticker": "t0", "date": "2026-01-01"}]
        st = run_horizon(signals, series, idx_map, 5, n_iter=200)
        assert st["n"] == 1 and st["excess"] > 0
        assert 0.0 < st["p"] <= 1.0

    def test_insufficient_cross_section(self):
        series = _series({f"t{k}": [100] * 12 for k in range(5)})
        idx_map = {t: {d: i for i, d in enumerate(s.dates)} for t, s in series.items()}
        st = run_horizon([{"ticker": "t0", "date": "2026-01-01"}], series, idx_map, 5)
        assert st["n"] == 0, "유니버스 30 미만 → 비교 불가"
