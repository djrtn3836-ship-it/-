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
)


def _days(n: int, end: str = "2026-10-19"):
    d0 = date.fromisoformat(end) - timedelta(days=n - 1)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


class TestNoLookahead:
    """폭 계산은 asof까지의 데이터만 사용해야 한다(룩어헤드 금지)."""

    def test_asof_uses_past_only(self):
        dates = _days(40)
        # 앞 25일 상승 → 이후 급락 15일
        closes = [100.0 + i for i in range(25)] + [124.0 - 6 * i for i in range(15)]
        series = {"A": S(dates, closes, [c + 1 for c in closes], [c - 1 for c in closes])}
        p_before = breadth_at(series, dates[24])   # 상승 구간 끝
        p_after = breadth_at(series, dates[-1])    # 급락 후
        assert p_before == 100.0
        assert p_after == 0.0

    def test_stale_series_excluded(self):
        dates = _days(40)
        up = S(dates, [100.0 + i for i in range(40)], [101.0 + i for i in range(40)], [99.0 + i for i in range(40)])
        old_dates = _days(40, end="2026-09-01")  # 7일+ 경과
        old = S(old_dates, [200.0 - i for i in range(40)], [201.0] * 40, [199.0] * 40)
        p = breadth_at({"up": up, "old": old}, dates[-1])
        # old 제외 → up만 → 100 (n부족이면 None) — n=1 < 20 → None
        assert p is None


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
