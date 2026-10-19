# -*- coding: utf-8 -*-
"""무상증자 진입/청산 검증(S142) 테스트."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from validation.disclosure_entry_test import (  # noqa: E402
    breadth_at,
    build_rows,
    has_artifact,
    load_series,
    stats,
    summarize,
)


def make_days(n=60, start="2022-01-03"):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


def flat_series(n=60, price=100.0):
    return [price] * n


class TestLoad:

    def test_skips_zero_close(self):
        rows = [
            {"date": "2022-01-03", "open": "99.5", "close": "100", "volume": "1000"},
            {"date": "2022-01-04", "open": "0", "close": "0", "volume": "5"},
        ]
        d, o, c, v = load_series(rows)
        assert len(d) == 1 and c[0] == 100.0 and o[0] == 99.5 and v[0] == 1000.0

    def test_artifact(self):
        closes = flat_series()
        closes[10] = 140.0
        assert has_artifact(closes, 5, 12) is True
        assert has_artifact(closes, 5, 9) is False


class TestBuildRows:

    def _rows(self, closes, opens=None, vols=None):
        days = make_days()
        opens = opens or [c - 0.5 for c in closes]
        vols = vols or [1000.0] * len(closes)
        ev = [{"date": "20220107", "title": "주요사항보고서(무상증자결정)"}]
        return build_rows("A", ev, days, opens, closes, vols, list(days), flat_series(), {})

    def test_entry_variants(self):
        closes = [100.0 + i for i in range(60)]
        rows = self._rows(closes)
        assert len(rows) == 1
        r = rows[0]
        assert r["base"] == 4
        assert abs(r["fwd"]["E0_5"] - (109 / 104 - 1)) < 1e-9
        assert abs(r["fwd"]["E1_5"] - (110 / 105 - 1)) < 1e-9
        assert abs(r["fwd"]["E2_5"] - (110 / 104.5 - 1)) < 1e-9

    def test_non_target_ignored(self):
        days = make_days()
        ev = [{"date": "20220107", "title": "주요사항보고서(유상증자결정)"}]
        assert build_rows("A", ev, days, [99.0] * 60, [100.0] * 60, [1.0] * 60, days, [100.0] * 60, {}) == []

    def test_stop_loss_hit(self):
        closes = [100.0] * 60
        closes[6] = 96.0
        r = self._rows(closes)[0]
        assert abs(r["exits"]["stop3"] - (-0.04)) < 1e-9

    def test_target_hit(self):
        closes = [100.0] * 60
        closes[6] = 108.0
        r = self._rows(closes)[0]
        assert abs(r["exits"]["target7"] - 0.08) < 1e-9

    def test_hold5_when_inside_band(self):
        closes = [100.0] * 60
        closes[6] = 101.0
        r = self._rows(closes)[0]
        assert "stop3" not in r["exits"] and "target7" not in r["exits"]
        assert abs(r["exits"]["hold5"] - 0.0) < 1e-9


class TestConditions:

    def test_breadth_prior_fallback(self):
        assert breadth_at({"2022-01-04": 10.0}, "2022-01-07") == 10.0
        assert breadth_at({}, "2022-01-07") is None

    def test_stats(self):
        s = stats([1.0, 3.0])
        assert s["n"] == 2 and s["mean"] == 2.0 and s["median"] == 2.0 and s["hit"] == 100.0

    def test_summarize_splits(self):
        rows = [{"fwd": {"E1_5": 0.02}, "breadth": 10.0, "kospi5_pre": -0.01, "vol_ratio": 3.0},
                {"fwd": {"E1_5": -0.01}, "breadth": 40.0, "kospi5_pre": 0.01, "vol_ratio": 1.0}]
        res = summarize(rows)
        assert res["n"] == 2
        assert res["conditions"]["breadth_le15"]["stats"]["n"] == 1
        assert res["conditions"]["vol_x2"]["stats"]["n"] == 1
        assert res["conditions"]["kospi5_down"]["stats"]["mean"] == 2.0
