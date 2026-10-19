# -*- coding: utf-8 -*-
"""상황조건 스캔(S136 ①) 헬퍼 테스트."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analytics.market_breadth import S  # noqa: E402
from validation.condition_sweep_test import (  # noqa: E402
    _first_days,
    _streak_days,
    evaluate_condition,
    verdict,
)


class TestHelpers:

    def test_first_days_dedupe(self):
        vals = {"d1": 30.0, "d2": 12.0, "d3": 12.5, "d4": 30.0, "d5": 11.0}
        assert _first_days(vals, lambda v: v <= 15) == ["d2", "d5"], "연속 첫날만"

    def test_streak_days(self):
        series = {"d1": -5.0, "d2": -3.0, "d3": -1.0, "d4": 2.0, "d5": -1.0, "d6": -2.0, "d7": -3.0}
        assert _streak_days(series, sorted(series), -1, 3) == ["d3", "d7"]

    def test_streak_break_resets(self):
        series = {"d1": -5.0, "d2": 1.0, "d3": -1.0, "d4": -2.0}
        assert _streak_days(series, sorted(series), -1, 3) == []


class TestEvaluate:

    def _index(self):
        # 100 → 60일 상승(100→160) 후 20일 보합 — 이벤트는 단순 날짜로 지정
        dates = [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(80)]
        closes = [100.0 + i for i in range(60)] + [160.0] * 20
        return S(dates, closes, [], [])

    def test_evaluate_condition(self):
        k = self._index()
        st = evaluate_condition(k, [k.dates[10], k.dates[40]], 5)
        assert st is not None and st["n"] == 2
        assert st["excess"] > 0, "상승 구간 이벤트 → 기준선 초과 양수"
        assert st["years_pos"].endswith("/1")

    def test_no_dates(self):
        k = self._index()
        assert evaluate_condition(k, [], 5) is None


class TestVerdict:

    def _st(self, **over):
        st = {"n": 15, "excess": 3.0, "hit": 70.0, "t": 2.5, "half1": 1.0, "half2": 2.0,
              "years_pos": "4/5", "years_ok": True}
        st.update(over)
        return st

    def test_survivor(self):
        assert verdict(self._st(), 0.005) == "생존 후보"

    def test_edges(self):
        assert verdict(self._st(), 0.03) == "경계"
        assert verdict(self._st(half1=-0.1), 0.005) == "경계", "생존 후보는 반기 일관성 필수 → 경계로 강등"
        assert verdict(self._st(n=5), 0.005) == "-", "n<10 탈락"
        assert verdict(self._st(years_ok=False), 0.005) == "-", "연도 일관성 실패"
