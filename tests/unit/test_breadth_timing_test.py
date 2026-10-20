"""시장 폭 레짐 검증 도구 테스트 (2026-10-19)."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from validation.breadth_timing_test import (  # noqa: E402
    S,
    breadth_at,
    find_events,
    fwd_return,
    latest_breadth,
    permutation_pvalue,
    threshold_events,
    threshold_sweep,
)


def _days(n: int, end: str = "2026-10-19"):
    d0 = date.fromisoformat(end) - timedelta(days=n - 1)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


class TestNoLookahead:
    """폭 계산은 asof까지의 데이터만 사용해야 한다(룩어헤드 금지)."""

    def test_asof_uses_past_only(self):
        dates = _days(40)
        # 앞 25일 상승 → 이후 급락 15일 (n>=20 요건 위해 25종목 동일 패턴)
        closes = [100.0 + i for i in range(25)] + [124.0 - 6 * i for i in range(15)]
        series = {
            f"t{k}": S(dates, list(closes), [c + 1 for c in closes], [c - 1 for c in closes])
            for k in range(25)
        }
        p_before = breadth_at(series, dates[24])   # 상승 구간 끝
        p_after = breadth_at(series, dates[-1])    # 급락 후
        assert p_before == 100.0
        assert p_after == 0.0

    def test_stale_series_excluded(self):
        dates = _days(40)
        up = [100.0 + i for i in range(40)]
        series = {
            f"t{k}": S(dates, list(up), [c + 1 for c in up], [c - 1 for c in up])
            for k in range(25)
        }
        old_dates = _days(40, end="2026-09-01")  # 7일+ 경과
        series["old"] = S(old_dates, [200.0 - i for i in range(40)], [201.0] * 40, [199.0] * 40)
        p = breadth_at(series, dates[-1])
        assert p == 100.0, "스테일 종목(하락 중)이 포함되면 96.2가 됨 — 제외 검증"


class TestEvents:

    def test_dedupe_turn_on(self):
        breadths = {
            "2026-01-01": 25.0, "2026-01-02": 15.0, "2026-01-03": 5.0,
            "2026-01-04": 3.0, "2026-01-05": 10.0, "2026-01-06": 25.0,
        }
        ev = find_events(breadths)
        assert ev["L1_폭20이하"] == ["2026-01-02"], "연속 지속일 중복 제거(첫날만)"
        assert ev["L2_폭10이하"] == ["2026-01-03"]

    def test_high_condition(self):
        ev = find_events({"d1": 50.0, "d2": 85.0, "d3": 90.0})
        assert ev["H1_폭80이상"] == ["d2"]


class TestLatestValueRegression:
    """2026-10-19 버그: 정렬 최댓값을 '현재'로 출력하던 문제."""

    def test_latest_is_by_date_not_max(self):
        breadths = {"2026-10-01": 91.0, "2026-10-19": 20.0}
        assert latest_breadth(breadths) == 20.0
        assert latest_breadth({}) is None


class TestForward:

    def test_fwd(self):
        assert abs(fwd_return([100.0, 110.0, 120.0], 0, 2) - 0.2) < 1e-9
        assert fwd_return([100.0, 110.0], 0, 5) is None


class TestRobustness:
    """S132: 임계 스윕·순열검정."""

    def test_threshold_events_dedupe(self):
        breadths = {"d1": 25.0, "d2": 12.0, "d3": 12.5, "d4": 20.0, "d5": 12.0}
        assert threshold_events(breadths, 15) == ["d2", "d5"], "연속 첫날만"
        assert threshold_events(breadths, 10) == []

    def test_threshold_sweep_n_ordering(self):
        dates = _days(120)
        # 0~100 사이 파형
        vals = [50.0 + 45.0 * ((i % 60) - 30) / 30.0 for i in range(120)]
        breadths = {d: v for d, v in zip(dates, vals, strict=False)}
        closes = [100.0 + i * 0.1 for i in range(120)]
        index = S(dates, closes, [c + 1 for c in closes], [c - 1 for c in closes])
        sw = threshold_sweep(index, breadths, 5, thresholds=(10, 50, 90))
        assert sw[10]["n"] <= sw[50]["n"] <= sw[90]["n"]
        assert all("excess" in st for st in sw.values() if st["n"])

    def test_permutation_extreme_and_null(self):
        dates = _days(120)
        closes = [100.0] * 120
        closes[55] = 50.0  # 하루 급락 후 원위치 → 전방 5일 +100%
        index = S(dates, closes, [c + 1 for c in closes], [c - 1 for c in closes])
        hot = permutation_pvalue(index, [dates[55]], 5, n_iter=500)
        cold = permutation_pvalue(index, [dates[20]], 5, n_iter=500)
        assert hot["obs"] > 50 and hot["p"] < 0.05
        assert cold["p"] > 0.3

    def test_permutation_deterministic_seed(self):
        dates = _days(120)
        closes = [100.0 + (i % 7) for i in range(120)]
        index = S(dates, closes, [c + 1 for c in closes], [c - 1 for c in closes])
        a = permutation_pvalue(index, [dates[10], dates[40]], 5, n_iter=200, seed=1)
        b = permutation_pvalue(index, [dates[10], dates[40]], 5, n_iter=200, seed=1)
        assert a == b
