"""
tests/unit/test_model_drift_detector.py - v1.0 (Session 10)
ModelDriftDetector 단위 테스트 (38개)
"""

import pytest

from observability.model_drift_detector import (
    DriftLevel,
    DriftReport,
    ModelDriftDetector,
    _WinRateTracker,
    compute_ks_statistic,
    compute_psi,
)


class TestComputePsi:
    def test_identical_distributions_zero(self):
        data = [0.1, 0.3, 0.5, 0.7, 0.9] * 10
        assert compute_psi(data, data) == pytest.approx(0.0, abs=1e-6)

    def test_completely_different_high_psi(self):
        assert compute_psi([0.1] * 50, [0.9] * 50) > 0.20

    def test_slight_shift_nonnegative(self):
        import random
        random.seed(42)
        expected = [random.uniform(0.4, 0.6) for _ in range(100)]
        actual = [random.uniform(0.5, 0.7) for _ in range(100)]
        assert compute_psi(expected, actual) >= 0.0

    def test_empty_expected_returns_zero(self):
        assert compute_psi([], [0.5] * 10) == 0.0

    def test_empty_actual_returns_zero(self):
        assert compute_psi([0.5] * 10, []) == 0.0

    def test_single_element_returns_zero(self):
        assert compute_psi([0.5], [0.5]) == 0.0

    def test_nonnegative_always(self):
        import random
        random.seed(0)
        for _ in range(10):
            a = [random.random() for _ in range(30)]
            b = [random.random() for _ in range(30)]
            assert compute_psi(a, b) >= 0.0

    def test_custom_bins(self):
        data = [i / 20.0 for i in range(20)]
        assert compute_psi(data, data, bins=5) == pytest.approx(0.0, abs=1e-6)
        assert compute_psi(data, data, bins=20) == pytest.approx(0.0, abs=1e-6)


class TestComputeKsStatistic:
    def test_identical_distributions_zero(self):
        data = [0.1 * i for i in range(10)]
        ks, _ = compute_ks_statistic(data, data)
        assert ks == pytest.approx(0.0, abs=1e-6)

    def test_different_distributions_high_ks(self):
        ks, _ = compute_ks_statistic([0.1] * 20, [0.9] * 20)
        assert ks > 0.8

    def test_returns_tuple(self):
        result = compute_ks_statistic([0.1, 0.5], [0.2, 0.6])
        assert isinstance(result, tuple) and len(result) == 2

    def test_ks_between_zero_and_one(self):
        import random
        random.seed(1)
        a = [random.random() for _ in range(30)]
        b = [random.random() for _ in range(30)]
        ks, pval = compute_ks_statistic(a, b)
        assert 0.0 <= ks <= 1.0
        assert 0.0 <= pval <= 1.0

    def test_empty_input_returns_zero(self):
        ks, pval = compute_ks_statistic([], [0.5])
        assert ks == 0.0
        assert pval == 1.0

    def test_symmetry(self):
        a = [0.1, 0.3, 0.5, 0.7]
        b = [0.2, 0.4, 0.6, 0.8]
        ks_ab, _ = compute_ks_statistic(a, b)
        ks_ba, _ = compute_ks_statistic(b, a)
        assert ks_ab == pytest.approx(ks_ba, abs=1e-6)


class TestWinRateTracker:
    def test_initial_win_rate_zero(self):
        assert _WinRateTracker(window_size=10).current_win_rate == 0.0

    def test_all_wins(self):
        t = _WinRateTracker(window_size=5)
        for _ in range(5):
            t.record(True)
        assert t.current_win_rate == pytest.approx(1.0)

    def test_all_losses(self):
        t = _WinRateTracker(window_size=5)
        for _ in range(5):
            t.record(False)
        assert t.current_win_rate == pytest.approx(0.0)

    def test_sliding_window_eviction(self):
        t = _WinRateTracker(window_size=3)
        t.record(True); t.record(True); t.record(True); t.record(False)
        assert t.current_win_rate == pytest.approx(2 / 3)

    def test_win_rate_drop(self):
        t = _WinRateTracker(window_size=10)
        t.set_baseline(0.6, 10)
        for _ in range(4):
            t.record(True)
        for _ in range(6):
            t.record(False)
        assert t.win_rate_drop < 0

    def test_reset_clears_window(self):
        t = _WinRateTracker(window_size=5)
        for _ in range(5):
            t.record(True)
        t.reset()
        assert t.sample_count == 0


class TestModelDriftDetectorInit:
    def test_default_init(self):
        d = ModelDriftDetector()
        assert d._window_size == 30
        assert d._baseline_size == 50
        assert d._psi_bins == 10

    def test_custom_params(self):
        d = ModelDriftDetector(window_size=10, baseline_size=20, psi_bins=5)
        assert d._window_size == 10
        assert d._baseline_size == 20
        assert d._psi_bins == 5

    def test_no_reports_initially(self):
        assert ModelDriftDetector().recent_drifts() == []


class TestModelDriftDetectorObserve:
    def test_no_report_during_baseline(self):
        d = ModelDriftDetector(baseline_size=10)
        for _ in range(9):
            assert d.observe("s1", 0.5, True) is None

    def test_no_report_after_baseline_stable(self):
        d = ModelDriftDetector(baseline_size=10, window_size=30)
        for i in range(10):
            d.observe("s1", 0.45 + 0.01 * i, True)
        last_report = None
        for i in range(30):
            r = d.observe("s1", 0.45 + 0.01 * (i % 10), True)
            if r is not None:
                last_report = r
        assert last_report is None

    def test_drift_detected_on_distribution_shift(self):
        d = ModelDriftDetector(baseline_size=50, window_size=30)
        for i in range(50):
            d.observe("s1", 0.4 + (i % 21) * 0.01, True)
        last_report = None
        for i in range(30):
            r = d.observe("s1", 0.7 + (i % 21) * 0.01, False)
            if r is not None:
                last_report = r
        assert last_report is not None

    def test_prediction_clipped_to_0_1(self):
        d = ModelDriftDetector(baseline_size=5)
        for _ in range(5):
            d.observe("s2", 1.5, True)
        assert d.win_rate("s2") == pytest.approx(1.0)

    def test_win_rate_tracked(self):
        d = ModelDriftDetector(baseline_size=5, window_size=10)
        for _ in range(5):
            d.observe("s3", 0.6, True)
        assert d.win_rate("s3") == pytest.approx(1.0)

    def test_psi_score_zero_before_baseline(self):
        assert ModelDriftDetector(baseline_size=50).psi_score("new_strategy") == 0.0

    def test_force_baseline_reset(self):
        d = ModelDriftDetector(baseline_size=10, window_size=10)
        for i in range(10):
            d.observe("s4", 0.5, True)
        for i in range(10):
            d.observe("s4", 0.9, False)
        d.force_baseline_reset("s4")
        assert d.strategy_status("s4")["baseline_samples"] == 10

    def test_multiple_strategies_independent(self):
        d = ModelDriftDetector(baseline_size=5)
        for _ in range(5):
            d.observe("a", 0.5, True)
            d.observe("b", 0.5, False)
        assert d.win_rate("a") == pytest.approx(1.0)
        assert d.win_rate("b") == pytest.approx(0.0)

    def test_observe_handles_nan_gracefully(self):
        # 원본 로그에는 assert가 누락된 채로 존재했던 테스트 → 실제 검증 로직으로 보강
        d = ModelDriftDetector(baseline_size=50)
        try:
            result = d.observe("s5", float("nan"), True)
        except Exception:
            pytest.fail("NaN 입력 시 예외가 발생해서는 안 됨")
        assert (result is None) or isinstance(result, DriftReport)


class TestDriftReport:
    def _make(self, level=DriftLevel.DRIFT, retrain=True):
        return DriftReport(
            strategy_name="test", drift_level=level, psi_score=0.25,
            ks_statistic=0.35, win_rate_current=0.45, win_rate_baseline=0.60,
            win_rate_drop=-0.15, should_retrain=retrain,
            drift_reason="PSI=0.25", sample_count=30,
        )

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for k in ["strategy_name", "drift_level", "psi_score", "should_retrain", "timestamp"]:
            assert k in d

    def test_drift_level_value_in_dict(self):
        assert self._make(level=DriftLevel.CRITICAL).to_dict()["drift_level"] == "CRITICAL"

    def test_frozen_immutability(self):
        with pytest.raises(Exception):
            object.__setattr__(self._make(), "psi_score", 0.99)

    def test_should_retrain_true_on_drift(self):
        assert self._make(level=DriftLevel.DRIFT, retrain=True).should_retrain is True


class TestStrategyStatus:
    def test_status_unknown_strategy(self):
        s = ModelDriftDetector().strategy_status("never_seen")
        assert s["strategy_name"] == "never_seen"
        assert s["baseline_ready"] is False

    def test_all_strategy_statuses_empty(self):
        assert isinstance(ModelDriftDetector().all_strategy_statuses(), list)
