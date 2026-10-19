"""재무(퀄리티) 팩터 검증 도구 테스트 (2026-10-19)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from validation.fundamentals_factor_test import (  # noqa: E402
    _to_metric_fields,
    entry_exit_dates,
    has_corporate_action,
    period_return,
    rank_groups,
    split_quintile_edges,
    tstat,
)


class TestDates:

    def test_entry_exit(self):
        assert entry_exit_dates("2024") == ("2025-04-01", "2026-04-01")

    def test_period_return(self):
        dates = ["2025-03-31", "2025-04-01", "2025-10-01", "2026-04-01", "2026-04-02"]
        closes = [100.0, 100.0, 120.0, 150.0, 160.0]
        r = period_return(dates, closes, "2024")
        assert abs(r - 0.5) < 1e-9  # 100 → 150

    def test_period_return_missing_exit(self):
        dates = ["2025-04-01", "2025-10-01"]
        closes = [100.0, 120.0]
        assert period_return(dates, closes, "2024") is None, "청산일 없으면 None(추정 금지)"


class TestCorporateAction:

    def test_split_detected(self):
        dates = ["2025-04-01", "2025-04-02", "2025-04-03"]
        closes = [100.0, 120.0, 30.0]  # -75% = 분할/감자 아티팩트
        assert has_corporate_action(dates, closes, "2025-04-01", "2026-04-01") is True

    def test_normal_move_ok(self):
        dates = ["2025-04-01", "2025-04-02", "2025-04-03"]
        closes = [100.0, 120.0, 90.0]  # +20%, -25% — 제한 내
        assert has_corporate_action(dates, closes, "2025-04-01", "2026-04-01") is False

    def test_jump_outside_window_ignored(self):
        dates = ["2024-01-01", "2024-01-02", "2025-04-01", "2025-04-02"]
        closes = [100.0, 20.0, 50.0, 51.0]  # 점프는 창 밖(2024)
        assert has_corporate_action(dates, closes, "2025-04-01", "2026-04-01") is False


class TestGroups:

    def test_quintile_edges(self):
        assert split_quintile_edges(100) == 20
        assert split_quintile_edges(4) == 1  # 최소 1

    def test_rank_groups_roe(self):
        vals = {f"t{i}": float(i) for i in range(10)}
        good, bad = rank_groups(vals, higher_is_better=True)
        assert set(good) == {"t9", "t8"} and set(bad) == {"t0", "t1"}

    def test_rank_groups_debt_lower_better(self):
        vals = {f"t{i}": float(i) for i in range(10)}
        good, bad = rank_groups(vals, higher_is_better=False)
        assert set(good) == {"t0", "t1"} and set(bad) == {"t8", "t9"}

    def test_rank_groups_too_small(self):
        assert rank_groups({"a": 1.0}, True) == ([], [])


class TestNormalize:

    def test_to_metric_fields(self):
        fin = {"ROE": 12.5, "부채비율": 30.0, "영업이익률": 8.1, "매출액": 1e12}
        out = _to_metric_fields(fin)
        assert out == {"roe": 12.5, "debt_ratio": 30.0, "op_margin": 8.1}

    def test_to_metric_fields_missing_kept_missing(self):
        assert _to_metric_fields({"ROE": None}) == {}
        assert _to_metric_fields({}) == {}

    def test_tstat_needs_variance(self):
        assert tstat([1.0, 1.0]) == 0.0
        assert tstat([1.0, 2.0, 3.0]) > 1.0
