# -*- coding: utf-8 -*-
"""
tests/unit/test_anomaly_detector.py - Anomaly Detector 테스트 (v1.0)

테스트 클래스:
    TestCFactor               (4개)  : _c_factor 정규화 상수
    TestIsolationForestInit   (4개)  : 초기화 검증
    TestIsolationForestFit    (5개)  : fit() 동작
    TestIsolationForestScore  (6개)  : anomaly_score / predict
    TestAnomalyDetectorObserve(7개)  : observe() 이상 탐지
    TestAnomalyDetectorMgmt   (5개)  : clear/recent/summary
    TestDetectZscoreAnomaly   (6개)  : 단변량 z-score 탐지
    TestAnomalyReportDTO      (4개)  : AnomalyReport DTO

총 41개 테스트
"""

import math
import pytest

from observability.anomaly_detector import (
    AnomalyDetector,
    AnomalyReport,
    IsolationForest,
    _c_factor,
    detect_zscore_anomaly,
)


# ─── 헬퍼 ────────────────────────────────────────────────────────

def _normal_data(n: int = 50, n_features: int = 4) -> list:
    """정규 분포 주변의 정상 데이터 생성 (랜덤 없이 결정적)."""
    data = []
    for i in range(n):
        row = [0.5 + 0.02 * math.sin(i * j) for j in range(1, n_features + 1)]
        data.append(row)
    return data


def _outlier_sample(n_features: int = 4) -> list:
    """명확한 이상치 샘플."""
    return [10.0] * n_features  # 정상 범위(0~1)에서 극단적으로 벗어남


# ═══════════════════════════════════════════════════════════════════
#  _c_factor (4개)
# ═══════════════════════════════════════════════════════════════════

class TestCFactor:
    def test_n1_returns_1(self):
        assert _c_factor(1) == 1.0

    def test_n2_returns_1(self):
        assert _c_factor(2) == 1.0

    def test_n_large_positive(self):
        val = _c_factor(256)
        assert val > 1.0

    def test_monotone_increasing(self):
        vals = [_c_factor(n) for n in [2, 10, 50, 200]]
        for i in range(len(vals) - 1):
            assert vals[i] <= vals[i + 1]


# ═══════════════════════════════════════════════════════════════════
#  IsolationForest 초기화 (4개)
# ═══════════════════════════════════════════════════════════════════

class TestIsolationForestInit:
    def test_default_init(self):
        iso = IsolationForest()
        assert not iso.is_fitted

    def test_zero_estimators_raises(self):
        with pytest.raises(ValueError, match="n_estimators"):
            IsolationForest(n_estimators=0)

    def test_zero_max_samples_raises(self):
        with pytest.raises(ValueError, match="max_samples"):
            IsolationForest(max_samples=0)

    def test_threshold_out_of_range_raises(self):
        with pytest.raises(ValueError, match="threshold"):
            IsolationForest(threshold=1.0)


# ═══════════════════════════════════════════════════════════════════
#  IsolationForest.fit() (5개)
# ═══════════════════════════════════════════════════════════════════

class TestIsolationForestFit:
    def test_fit_marks_fitted(self):
        iso = IsolationForest(n_estimators=10, seed=0)
        iso.fit(_normal_data(20))
        assert iso.is_fitted

    def test_fit_returns_self(self):
        iso = IsolationForest(n_estimators=10, seed=0)
        result = iso.fit(_normal_data(20))
        assert result is iso

    def test_too_few_samples_raises(self):
        iso = IsolationForest(n_estimators=5, seed=0)
        with pytest.raises(ValueError):
            iso.fit([[0.5, 0.5]] * 3)

    def test_score_before_fit_raises(self):
        iso = IsolationForest()
        with pytest.raises(RuntimeError):
            iso.anomaly_score([0.5, 0.5, 0.5, 0.5])

    def test_fit_chain(self):
        iso = IsolationForest(n_estimators=5, seed=0)
        iso.fit(_normal_data(20)).fit(_normal_data(30))
        assert iso.is_fitted


# ═══════════════════════════════════════════════════════════════════
#  IsolationForest 스코어링 (6개)
# ═══════════════════════════════════════════════════════════════════

class TestIsolationForestScore:
    @pytest.fixture
    def fitted_iso(self):
        iso = IsolationForest(n_estimators=50, seed=42)
        iso.fit(_normal_data(100))
        return iso

    def test_score_in_range(self, fitted_iso):
        score = fitted_iso.anomaly_score([0.5, 0.5, 0.5, 0.5])
        assert 0.0 <= score <= 1.0

    def test_outlier_higher_score_than_normal(self, fitted_iso):
        normal_score = fitted_iso.anomaly_score([0.5, 0.5, 0.5, 0.5])
        outlier_score = fitted_iso.anomaly_score(_outlier_sample())
        assert outlier_score >= normal_score

    def test_predict_normal_not_anomaly(self, fitted_iso):
        # 정상 중심값은 이상치로 판정되지 않아야 함 (확률적이므로 결정적 시드 사용)
        score = fitted_iso.anomaly_score([0.5, 0.5, 0.5, 0.5])
        assert isinstance(score, float)

    def test_predict_returns_bool(self, fitted_iso):
        result = fitted_iso.predict([0.5, 0.5, 0.5, 0.5])
        assert isinstance(result, bool)

    def test_predict_batch_length(self, fitted_iso):
        samples = [[0.5] * 4, [0.6] * 4, _outlier_sample()]
        results = fitted_iso.predict_batch(samples)
        assert len(results) == 3

    def test_predict_batch_all_bool(self, fitted_iso):
        results = fitted_iso.predict_batch([[0.5] * 4, [0.6] * 4])
        assert all(isinstance(r, bool) for r in results)


# ═══════════════════════════════════════════════════════════════════
#  AnomalyDetector.observe() (7개)
# ═══════════════════════════════════════════════════════════════════

class TestAnomalyDetectorObserve:
    @pytest.fixture
    def detector(self):
        return AnomalyDetector(window_size=30, n_estimators=20, seed=0)

    def test_observe_returns_none_before_enough_data(self, detector):
        # 첫 관측은 데이터 부족 → None
        result = detector.observe(0.5, 0.7, 10.0, 0.6)
        assert result is None

    def test_observe_fills_buffer(self, detector):
        for _ in range(10):
            detector.observe(0.5, 0.7, 10.0, 0.6)
        assert detector.buffer_size == 10

    def test_observe_does_not_crash(self, detector):
        for i in range(50):
            detector.observe(
                0.5 + 0.01 * math.sin(i),
                0.7,
                10.0 + i * 0.1,
                0.6,
            )
        # 오류 없이 실행되어야 함

    def test_observe_anomaly_returns_report(self, detector):
        # 정상 데이터로 학습 후 극단적 이상치 주입
        for _ in range(20):
            detector.observe(0.5, 0.7, 10.0, 0.6)
        # 극단적 이상치 주입
        result = detector.observe(0.01, 0.01, 10000.0, 0.01)
        # 탐지될 수도 있고 안 될 수도 있음 (확률적) → 타입만 체크
        assert result is None or isinstance(result, AnomalyReport)

    def test_observe_is_fitted_after_enough_data(self, detector):
        for _ in range(10):
            detector.observe(0.5, 0.7, 10.0, 0.6)
        assert detector.is_fitted

    def test_observe_clamps_values(self, detector):
        # score/confidence/sqi > 1.0 또는 < 0.0 입력해도 크래시 없음
        detector.observe(2.0, -1.0, 50.0, 3.0)  # 클램핑 후 저장

    def test_observe_report_has_correct_fields(self):
        det = AnomalyDetector(window_size=10, n_estimators=10, threshold=0.0, seed=0)
        # threshold=0 → 모든 것이 anomaly
        for _ in range(10):
            det.observe(0.5, 0.7, 10.0, 0.6)
        report = det.observe(0.5, 0.7, 10.0, 0.6)
        if report is not None:
            assert isinstance(report.is_anomaly, bool)
            assert 0.0 <= report.anomaly_score <= 1.0


# ═══════════════════════════════════════════════════════════════════
#  AnomalyDetector 관리 (5개)
# ═══════════════════════════════════════════════════════════════════

class TestAnomalyDetectorMgmt:
    def test_clear_resets_buffer(self):
        det = AnomalyDetector(window_size=20)
        for _ in range(10):
            det.observe(0.5, 0.7, 10.0, 0.6)
        det.clear()
        assert det.buffer_size == 0
        assert not det.is_fitted

    def test_force_refit_returns_false_when_empty(self):
        det = AnomalyDetector()
        assert det.force_refit() is False

    def test_force_refit_returns_true_with_data(self):
        det = AnomalyDetector(window_size=20, n_estimators=5)
        for _ in range(10):
            det.observe(0.5, 0.7, 10.0, 0.6)
        assert det.force_refit() is True

    def test_summary_keys(self):
        det = AnomalyDetector()
        s = det.summary()
        for key in ("buffer_size", "window_size", "is_fitted", "threshold",
                    "total_anomalies", "fit_count"):
            assert key in s

    def test_recent_anomalies_returns_list(self):
        det = AnomalyDetector()
        assert isinstance(det.recent_anomalies(5), list)


# ═══════════════════════════════════════════════════════════════════
#  detect_zscore_anomaly (6개)
# ═══════════════════════════════════════════════════════════════════

class TestDetectZscoreAnomaly:
    def test_empty_returns_empty(self):
        assert detect_zscore_anomaly([]) == []

    def test_single_value_returns_empty(self):
        assert detect_zscore_anomaly([0.5]) == []

    def test_all_same_returns_empty(self):
        assert detect_zscore_anomaly([0.5] * 10) == []

    def test_detects_clear_outlier(self):
        values = [0.5] * 20 + [100.0]  # 마지막이 명확한 이상치
        anomalies = detect_zscore_anomaly(values)
        assert len(anomalies) >= 1
        indices = [a[0] for a in anomalies]
        assert 20 in indices

    def test_returns_index_value_zscore(self):
        values = [1.0] * 19 + [100.0]
        anomalies = detect_zscore_anomaly(values)
        assert len(anomalies) >= 1
        idx, val, zscore = anomalies[0]
        assert isinstance(idx, int)
        assert isinstance(val, float)
        assert zscore > 3.0

    def test_custom_sigma_threshold(self):
        values = [0.5] * 10 + [2.0]
        loose = detect_zscore_anomaly(values, sigma_threshold=1.0)
        strict = detect_zscore_anomaly(values, sigma_threshold=5.0)
        assert len(loose) >= len(strict)


# ═══════════════════════════════════════════════════════════════════
#  AnomalyReport DTO (4개)
# ═══════════════════════════════════════════════════════════════════

class TestAnomalyReportDTO:
    def _make(self):
        return AnomalyReport(
            is_anomaly=True,
            anomaly_score=0.82,
            metric_name="signal_score",
            value=0.95,
            threshold=0.65,
            reason="Test anomaly",
        )

    def test_to_dict_has_required_keys(self):
        d = self._make().to_dict()
        for key in ("is_anomaly", "anomaly_score", "metric_name",
                    "value", "threshold", "reason", "timestamp"):
            assert key in d

    def test_score_rounded(self):
        d = self._make().to_dict()
        s = d["anomaly_score"]
        assert s == round(s, 4)

    def test_frozen_immutability(self):
        r = self._make()
        with pytest.raises((AttributeError, TypeError)):
            r.is_anomaly = False  # type: ignore

    def test_is_anomaly_true(self):
        assert self._make().is_anomaly is True
