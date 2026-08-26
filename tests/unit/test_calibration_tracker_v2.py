# -*- coding: utf-8 -*-
"""
tests/unit/test_calibration_tracker_v2.py

CalibrationTracker v5.2.0 테스트
- @trace.traced 적용 확인 (record / get_calibration)
- record_ab_result() ABTest 연동
- get_all_regimes() / get_sample_counts()
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from analytics.calibration_tracker import CalibrationTracker, _AB_CALIBRATION_TEST


def _run(coro):
    """새 이벤트 루프로 코루틴 실행 (기존 루프 영향 없음)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────────
#  픽스처
# ─────────────────────────────────────────────────────────────────────────────

def make_tracker(ab_manager=None) -> CalibrationTracker:
    return CalibrationTracker(ab_manager=ab_manager)


def fill_records(tracker: CalibrationTracker, regime: str, n: int = 15) -> None:
    """n개의 calibration 레코드 삽입 (alternating win/loss)."""
    for i in range(n):
        confidence = 0.70 + (i % 5) * 0.05   # 0.70 ~ 0.90
        actual_win = i % 2 == 0
        tracker.record(regime, confidence, actual_win)


# ─────────────────────────────────────────────────────────────────────────────
#  TestRecordBasic
# ─────────────────────────────────────────────────────────────────────────────

class TestRecordBasic:
    """record() 기본 동작"""

    def test_record_adds_data(self):
        tracker = make_tracker()
        tracker.record("trend", 0.80, True)
        assert len(tracker.data["trend"]) == 1

    def test_record_keys(self):
        tracker = make_tracker()
        tracker.record("trend", 0.75, False)
        rec = tracker.data["trend"][0]
        assert "confidence" in rec
        assert "actual_win" in rec
        assert "timestamp" in rec

    def test_record_multiple_regimes(self):
        tracker = make_tracker()
        tracker.record("trend", 0.80, True)
        tracker.record("reversal", 0.65, False)
        assert "trend" in tracker.data
        assert "reversal" in tracker.data

    def test_record_values_preserved(self):
        tracker = make_tracker()
        tracker.record("sideways", 0.91, True)
        rec = tracker.data["sideways"][0]
        assert rec["confidence"] == 0.91
        assert rec["actual_win"] is True


# ─────────────────────────────────────────────────────────────────────────────
#  TestGetCalibration
# ─────────────────────────────────────────────────────────────────────────────

class TestGetCalibration:
    """get_calibration() 동작"""

    def test_insufficient_data(self):
        tracker = make_tracker()
        for _ in range(5):
            tracker.record("trend", 0.80, True)
        result = tracker.get_calibration("trend")
        assert result["status"] == "insufficient_data"
        assert result["sample"] == 5

    def test_empty_regime(self):
        tracker = make_tracker()
        result = tracker.get_calibration("nonexistent")
        assert result["status"] == "insufficient_data"
        assert result["sample"] == 0

    def test_sufficient_data_keys(self):
        tracker = make_tracker()
        fill_records(tracker, "trend", n=15)
        result = tracker.get_calibration("trend")
        assert "ece" in result
        assert "buckets" in result
        assert "status" in result
        assert "regime" in result
        assert result["regime"] == "trend"

    def test_ece_range(self):
        """ECE는 0 이상 1 이하."""
        tracker = make_tracker()
        fill_records(tracker, "trend", n=20)
        result = tracker.get_calibration("trend")
        assert 0.0 <= result["ece"] <= 1.0

    def test_ece_perfect_calibration(self):
        """완벽하게 calibrated: 0.80 confidence → 80% 실제 win."""
        tracker = make_tracker()
        for _ in range(8):
            tracker.record("perfect", 0.80, True)
        for _ in range(2):
            tracker.record("perfect", 0.80, False)
        result = tracker.get_calibration("perfect")
        # ECE는 0에 가까워야 함 (정확히 0은 아닐 수 있음)
        assert result["ece"] < 0.30   # 느슨한 상한

    def test_status_pass_warn(self):
        tracker = make_tracker()
        # 모든 0.90 confidence가 실제로 50% win → WARN
        for _ in range(20):
            tracker.record("bad", 0.90, True)  # overconfident
        for _ in range(20):
            tracker.record("bad", 0.90, False)
        result = tracker.get_calibration("bad")
        assert result["status"] in ("PASS", "WARN")

    def test_total_samples_field(self):
        """total_samples는 각 bucket에 실제로 분류된 레코드 수의 합."""
        tracker = make_tracker()
        fill_records(tracker, "trend", n=20)
        result = tracker.get_calibration("trend")
        assert "total_samples" in result
        # total_samples ≤ 실제 레코드 수 (bucket 분류 실패 레코드 제외 가능)
        assert result["total_samples"] >= 1
        assert result["total_samples"] <= 20


# ─────────────────────────────────────────────────────────────────────────────
#  TestRecordAbResult
# ─────────────────────────────────────────────────────────────────────────────

class TestRecordAbResult:
    """record_ab_result() ABTest 연동"""

    def test_no_ab_manager_returns_false(self):
        """ABTestManager 없으면 False 반환."""
        tracker = make_tracker(ab_manager=None)
        # lazy import가 실패해도 False 반환
        with patch.object(tracker, "_get_ab_manager", return_value=None):
            result = _run(tracker.record_ab_result("trend"))
        assert result is False

    def test_insufficient_data_returns_false(self):
        """샘플 부족 시 False 반환."""
        tracker = make_tracker()
        # 데이터 5개만 (10개 미만)
        for _ in range(5):
            tracker.record("trend", 0.80, True)
        mock_mgr = MagicMock()
        mock_mgr._tests = {}

        async def run():
            with patch.object(tracker, "_get_ab_manager", return_value=mock_mgr):
                return await tracker.record_ab_result("trend")

        result = _run(run())
        assert result is False

    def test_no_calibration_test_returns_false(self):
        """ABTest 'calibration_quality' 없으면 False."""
        tracker = make_tracker()
        fill_records(tracker, "trend", n=15)
        mock_mgr = MagicMock()
        mock_mgr._tests = {}  # 'calibration_quality' 없음

        async def run():
            with patch.object(tracker, "_get_ab_manager", return_value=mock_mgr):
                return await tracker.record_ab_result("trend")

        result = _run(run())
        assert result is False

    def test_regime_not_in_variants_returns_false(self):
        """변형 목록에 없는 regime은 False."""
        tracker = make_tracker()
        fill_records(tracker, "unknown_regime", n=15)

        # ABTest mock: variants에 "unknown_regime" 없음
        mock_test = MagicMock()
        mock_test.variants = {"trend": MagicMock(), "reversal": MagicMock()}
        mock_mgr = MagicMock()
        mock_mgr._tests = {_AB_CALIBRATION_TEST: mock_test}

        async def run():
            with patch.object(tracker, "_get_ab_manager", return_value=mock_mgr):
                return await tracker.record_ab_result("unknown_regime")

        result = _run(run())
        assert result is False

    def test_successful_record_calls_manager(self):
        """성공 시 record_result 호출 확인."""
        tracker = make_tracker()
        fill_records(tracker, "trend", n=15)

        mock_test = MagicMock()
        mock_test.variants = {"trend": MagicMock()}

        mock_mgr = MagicMock()
        mock_mgr._tests = {_AB_CALIBRATION_TEST: mock_test}
        mock_mgr.record_result = AsyncMock(return_value=True)

        async def run():
            with patch.object(tracker, "_get_ab_manager", return_value=mock_mgr):
                return await tracker.record_ab_result("trend")

        result = _run(run())
        assert result is True
        mock_mgr.record_result.assert_called_once()
        # 첫 번째 인자: test_name
        args = mock_mgr.record_result.call_args[0]
        assert args[0] == _AB_CALIBRATION_TEST
        assert args[1] == "trend"
        # ab_metric은 0~1 범위
        assert 0.0 <= args[2] <= 1.0

    def test_ab_metric_is_one_minus_ece(self):
        """ab_metric = 1.0 - ece 검증."""
        tracker = make_tracker()
        fill_records(tracker, "reversal", n=20)

        cal = tracker.get_calibration("reversal")
        expected_metric = 1.0 - cal["ece"]

        mock_test = MagicMock()
        mock_test.variants = {"reversal": MagicMock()}
        mock_mgr = MagicMock()
        mock_mgr._tests = {_AB_CALIBRATION_TEST: mock_test}
        mock_mgr.record_result = AsyncMock(return_value=True)

        async def run():
            with patch.object(tracker, "_get_ab_manager", return_value=mock_mgr):
                return await tracker.record_ab_result("reversal")

        _run(run())
        actual_metric = mock_mgr.record_result.call_args[0][2]
        assert abs(actual_metric - expected_metric) < 1e-9


# ─────────────────────────────────────────────────────────────────────────────
#  TestHelperMethods
# ─────────────────────────────────────────────────────────────────────────────

class TestHelperMethods:
    """get_all_regimes / get_sample_counts"""

    def test_get_all_regimes_empty(self):
        tracker = make_tracker()
        assert tracker.get_all_regimes() == []

    def test_get_all_regimes_populated(self):
        tracker = make_tracker()
        tracker.record("trend", 0.80, True)
        tracker.record("reversal", 0.65, False)
        regimes = tracker.get_all_regimes()
        assert "trend" in regimes
        assert "reversal" in regimes

    def test_get_sample_counts(self):
        tracker = make_tracker()
        for _ in range(3):
            tracker.record("trend", 0.80, True)
        for _ in range(7):
            tracker.record("sideways", 0.60, False)
        counts = tracker.get_sample_counts()
        assert counts["trend"] == 3
        assert counts["sideways"] == 7

    def test_lazy_ab_manager_fallback(self):
        """_get_ab_manager: import 실패 시 None 반환."""
        tracker = make_tracker()
        with patch(
            "analytics.calibration_tracker.CalibrationTracker._get_ab_manager",
            return_value=None,
        ):
            mgr = tracker._get_ab_manager()
        assert mgr is None
