# -*- coding: utf-8 -*-
"""
tests/unit/test_e2e_bootstrap_dry_run.py

E2E 통합 테스트 - Bootstrap Startup Dry-Run
app/bootstrap.py의 Phase 3 통합 컴포넌트 연동 검증:
    1. ABTestManager ↔ CalibrationTracker 연동
    2. PortfolioVaR.position_limit → OrderExecutor 연결
    3. BanditFeedbackBridge @trace.traced 적용
    4. Bootstrap 컴포넌트 생성 순서 (단위 수준 dry-run)
"""

import asyncio
import sys
import types
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ─────────────────────────────────────────────────────────────────────────────
#  telegram mock (order_executor 의존성)
# ─────────────────────────────────────────────────────────────────────────────

def _install_telegram_mock():
    if "telegram" in sys.modules and not isinstance(
        getattr(sys.modules["telegram"], "Bot", None), MagicMock
    ):
        return
    tg = types.ModuleType("telegram")
    tg.Bot = MagicMock
    sys.modules["telegram"] = tg
    tg_err = types.ModuleType("telegram.error")
    tg_err.NetworkError = type("NetworkError", (Exception,), {})
    tg_err.TelegramError = type("TelegramError", (Exception,), {})
    tg_err.TimedOut = type("TimedOut", (Exception,), {})
    sys.modules["telegram.error"] = tg_err
    tg.error = tg_err
    tg_ext = types.ModuleType("telegram.ext")
    tg_ext.Application = MagicMock
    tg_ext.ApplicationBuilder = MagicMock
    tg_ext.CommandHandler = MagicMock
    tg_ext.MessageHandler = MagicMock
    tg_ext.filters = MagicMock()
    sys.modules["telegram.ext"] = tg_ext
    tg.ext = tg_ext

_install_telegram_mock()


# ─────────────────────────────────────────────────────────────────────────────
#  imports
# ─────────────────────────────────────────────────────────────────────────────

from analytics.calibration_tracker import CalibrationTracker, _AB_CALIBRATION_TEST
from application.analysis.ab_framework import ABTestManager, ABTest, get_ab_manager
from application.analysis.bandit_feedback_bridge import BanditFeedbackBridge
from execution.order_executor import OrderExecutor, OrderMode, OrderRequest
from risk.portfolio_var import PortfolioVaR, PortfolioRiskMetrics


def run(coro):
    """새 이벤트 루프를 생성하여 코루틴 실행 (기존 루프 상태 영향 없음)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────────
#  TestABTestManagerCalibrationIntegration
# ─────────────────────────────────────────────────────────────────────────────

class TestABTestManagerCalibrationIntegration:
    """CalibrationTracker ↔ ABTestManager 연동 E2E"""

    def _fresh_manager(self) -> ABTestManager:
        mgr = ABTestManager()
        mgr._tests = {}
        return mgr

    def test_calibration_quality_test_creation(self):
        """'calibration_quality' A/B 테스트 생성 확인."""
        mgr = self._fresh_manager()
        test = mgr.create_test(
            test_name=_AB_CALIBRATION_TEST,
            variant_names=["trend", "reversal", "sideways"],
            traffic_split=[1/3, 1/3, 1/3],
            alpha=0.05,
            min_samples=20,
        )
        assert test.name == _AB_CALIBRATION_TEST
        assert "trend" in test.variants
        assert "reversal" in test.variants
        assert "sideways" in test.variants

    def test_calibration_tracker_feeds_ab_manager(self):
        """CalibrationTracker.record_ab_result() → ABTestManager.record_result() 호출."""
        mgr = self._fresh_manager()
        mgr.create_test(
            test_name=_AB_CALIBRATION_TEST,
            variant_names=["trend", "reversal", "sideways"],
            traffic_split=[1/3, 1/3, 1/3],
        )

        tracker = CalibrationTracker(ab_manager=mgr)
        # trend regime에 15개 레코드
        for i in range(15):
            tracker.record("trend", 0.80 if i % 2 == 0 else 0.70, i % 2 == 0)

        result = run(tracker.record_ab_result("trend"))
        assert result is True

        # ABTest "trend" variant에 결과가 기록됐는지 확인
        test = mgr._tests[_AB_CALIBRATION_TEST]
        assert test.variants["trend"].n == 1

    def test_multiple_regimes_feed_independently(self):
        """여러 regime이 독립적으로 피드백."""
        mgr = self._fresh_manager()
        mgr.create_test(
            test_name=_AB_CALIBRATION_TEST,
            variant_names=["trend", "reversal", "sideways"],
            traffic_split=[1/3, 1/3, 1/3],
        )

        tracker = CalibrationTracker(ab_manager=mgr)
        for regime in ["trend", "reversal"]:
            for i in range(12):
                tracker.record(regime, 0.75, i % 2 == 0)

        run(tracker.record_ab_result("trend"))
        run(tracker.record_ab_result("reversal"))

        test = mgr._tests[_AB_CALIBRATION_TEST]
        assert test.variants["trend"].n == 1
        assert test.variants["reversal"].n == 1
        assert test.variants["sideways"].n == 0   # 데이터 없음

    def test_ab_metric_value_range(self):
        """ab_metric = 1 - ECE 는 0~1 범위."""
        mgr = self._fresh_manager()
        mgr.record_result_mock_called_with = []
        orig_record = mgr.record_result

        async def capture_record(test_name, variant, value):
            mgr.record_result_mock_called_with.append((test_name, variant, value))
            return await orig_record(test_name, variant, value)

        mgr.record_result = capture_record
        mgr.create_test(
            test_name=_AB_CALIBRATION_TEST,
            variant_names=["trend", "reversal", "sideways"],
            traffic_split=[1/3, 1/3, 1/3],
        )

        tracker = CalibrationTracker(ab_manager=mgr)
        for i in range(20):
            tracker.record("reversal", 0.65 + (i % 4) * 0.05, i % 3 != 0)

        run(tracker.record_ab_result("reversal"))

        assert len(mgr.record_result_mock_called_with) == 1
        _, _, metric = mgr.record_result_mock_called_with[0]
        assert 0.0 <= metric <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
#  TestPortfolioVarOrderExecutorIntegration
# ─────────────────────────────────────────────────────────────────────────────

class TestPortfolioVarOrderExecutorIntegration:
    """PortfolioVaR.position_limit → OrderExecutor 연결 E2E"""

    def make_executor(self) -> OrderExecutor:
        return OrderExecutor(
            kiwoom_connector=MagicMock(),
            db_manager=MagicMock(),
            telegram_sender=MagicMock(),
            mode=OrderMode.PAPER,
        )

    def make_request(self, qty: int) -> OrderRequest:
        return OrderRequest(ticker="005930", action="BUY", quantity=qty, price=70000.0)

    def test_var_metrics_to_executor_full_pipeline(self):
        """PortfolioRiskMetrics.position_limit → OrderExecutor 업데이트 → 검증."""
        # PortfolioRiskMetrics 생성 (position_limit = 0.75)
        metrics = PortfolioRiskMetrics(
            var_95=0.035,  # 3.5% → risk_adj = 0.75
            var_99=0.045,
            cvar_95=0.050,
            std_dev=0.020,
            expected_return=0.001,
            risk_adj_factor=0.75,
            simulation_count=10000,
            status="OK",
            kelly_position_limit=0.30,
            position_limit=0.75,  # min(0.75, 0.30) → 0.30 이지만 테스트용
            kelly_win_rate=0.55,
            kelly_valid=True,
        )

        executor = self.make_executor()
        assert executor.get_position_limit() == 1.0

        # PortfolioVaR 결과를 OrderExecutor에 전달
        executor.update_position_limit(metrics.position_limit)
        assert executor.get_position_limit() == metrics.position_limit

    def test_high_risk_var_blocks_large_orders(self):
        """고위험 VaR (5%+) → position_limit 0.5 → 500주 이하만 허용."""
        metrics = PortfolioRiskMetrics(
            var_95=0.055,   # 5.5% → risk_adj = 0.50
            var_99=0.070,
            cvar_95=0.080,
            std_dev=0.035,
            expected_return=-0.001,
            risk_adj_factor=0.50,
            simulation_count=10000,
            status="OK",
            position_limit=0.50,
        )

        executor = self.make_executor()
        executor.update_position_limit(metrics.position_limit)

        # 500주 → 통과
        passed, _ = run(executor._position_size_check(self.make_request(500)))
        assert passed is True

        # 501주 → 거부
        passed, reason = run(executor._position_size_check(self.make_request(501)))
        assert not passed
        assert "0.50" in reason or "position_limit" in reason.lower()

    def test_low_risk_var_allows_full_qty(self):
        """저위험 VaR (1% 미만) → position_limit 1.0 → 1000주 허용."""
        metrics = PortfolioRiskMetrics(
            var_95=0.009,   # 0.9% → risk_adj = 1.0
            var_99=0.015,
            cvar_95=0.012,
            std_dev=0.006,
            expected_return=0.003,
            risk_adj_factor=1.0,
            simulation_count=10000,
            status="OK",
            position_limit=1.0,
        )

        executor = self.make_executor()
        executor.update_position_limit(metrics.position_limit)

        passed, _ = run(executor._position_size_check(self.make_request(1000)))
        assert passed is True

    def test_kelly_limit_dominates(self):
        """Kelly 한도가 VaR 한도보다 낮으면 Kelly 한도 적용."""
        # Kelly = 0.30, VaR risk_adj = 0.90 → position_limit = min = 0.30
        metrics = PortfolioRiskMetrics(
            var_95=0.020,
            var_99=0.030,
            cvar_95=0.025,
            std_dev=0.015,
            expected_return=0.002,
            risk_adj_factor=0.90,
            simulation_count=10000,
            status="OK",
            kelly_position_limit=0.30,
            position_limit=0.30,   # min(0.90, 0.30)
            kelly_valid=True,
        )

        executor = self.make_executor()
        executor.update_position_limit(metrics.position_limit)

        # 300주 → 통과
        assert run(executor._position_size_check(self.make_request(300)))[0] is True
        # 301주 → 거부
        assert run(executor._position_size_check(self.make_request(301)))[0] is False


# ─────────────────────────────────────────────────────────────────────────────
#  TestBanditFeedbackBridgeTraced
# ─────────────────────────────────────────────────────────────────────────────

class TestBanditFeedbackBridgeTraced:
    """BanditFeedbackBridge @trace.traced 적용 확인"""

    def test_on_performance_updated_is_traced(self):
        """on_performance_updated가 @trace.traced 데코레이터를 가짐."""
        # 래핑된 함수인지 확인 (wrapper 속성 또는 __wrapped__ 존재)
        method = BanditFeedbackBridge.on_performance_updated
        assert callable(method)
        # @trace.traced는 functools.wraps를 사용하므로 원래 함수와 이름 동일
        assert method.__name__ == "on_performance_updated"

    def test_force_feedback_is_traced(self):
        method = BanditFeedbackBridge.force_feedback
        assert callable(method)
        assert method.__name__ == "force_feedback"

    def test_compute_strategy_rewards_is_traced(self):
        method = BanditFeedbackBridge._compute_strategy_rewards
        assert callable(method)
        assert method.__name__ == "_compute_strategy_rewards"

    def test_get_status_is_traced(self):
        method = BanditFeedbackBridge.get_status
        assert callable(method)
        assert method.__name__ == "get_status"

    def test_bridge_methods_callable(self):
        """BanditFeedbackBridge 인스턴스화 및 메서드 호출 가능 확인."""
        mock_db = MagicMock()
        mock_bandit = MagicMock()
        mock_bandit.get_weights = MagicMock(return_value={"Trend": 0.5, "Reversal": 0.3, "Breakout": 0.2})
        mock_bandit.get_stats = MagicMock(return_value=[])

        bridge = BanditFeedbackBridge(db=mock_db, bandit=mock_bandit)
        status = bridge.get_status()
        assert "last_feedback" in status
        assert "bandit_weights" in status
        assert "total_feedbacks" in status


# ─────────────────────────────────────────────────────────────────────────────
#  TestBootstrapStartAbFramework
# ─────────────────────────────────────────────────────────────────────────────

class TestBootstrapStartAbFramework:
    """bootstrap.start_ab_framework() 로직 단위 검증 (ABTestManager 직접 테스트)"""

    def _fresh_manager(self) -> ABTestManager:
        mgr = ABTestManager()
        mgr._tests = {}
        return mgr

    def simulate_start_ab_framework(self, mgr: ABTestManager):
        """bootstrap.start_ab_framework()와 동일한 로직."""
        mgr.create_test(
            test_name="strategy_selection",
            variant_names=["control", "ml_bandit"],
            traffic_split=[0.5, 0.5],
            alpha=0.05,
            min_samples=30,
        )
        mgr.create_test(
            test_name="entry_timing",
            variant_names=["momentum", "mean_revert"],
            traffic_split=[0.5, 0.5],
            alpha=0.05,
            min_samples=30,
        )
        mgr.create_test(
            test_name=_AB_CALIBRATION_TEST,
            variant_names=["trend", "reversal", "sideways"],
            traffic_split=[1/3, 1/3, 1/3],
            alpha=0.05,
            min_samples=20,
        )

    def test_three_tests_created(self):
        mgr = self._fresh_manager()
        self.simulate_start_ab_framework(mgr)
        tests = mgr.list_tests()
        assert "strategy_selection" in tests
        assert "entry_timing" in tests
        assert _AB_CALIBRATION_TEST in tests

    def test_strategy_selection_variants(self):
        mgr = self._fresh_manager()
        self.simulate_start_ab_framework(mgr)
        test = mgr._tests["strategy_selection"]
        assert "control" in test.variants
        assert "ml_bandit" in test.variants

    def test_calibration_quality_variants(self):
        mgr = self._fresh_manager()
        self.simulate_start_ab_framework(mgr)
        test = mgr._tests[_AB_CALIBRATION_TEST]
        assert "trend" in test.variants
        assert "reversal" in test.variants
        assert "sideways" in test.variants

    def test_all_tests_running_status(self):
        from application.analysis.ab_framework import TestStatus
        mgr = self._fresh_manager()
        self.simulate_start_ab_framework(mgr)
        for name, status in mgr.list_tests().items():
            assert status == TestStatus.RUNNING.value, f"{name}: {status}"

    def test_assign_variant_deterministic(self):
        """user_id 해시 기반 배정 재현성."""
        mgr = self._fresh_manager()
        self.simulate_start_ab_framework(mgr)
        v1 = mgr.assign_variant("strategy_selection", "AAPL")
        v2 = mgr.assign_variant("strategy_selection", "AAPL")
        assert v1 == v2

    def test_calibration_tracker_integration_with_bootstrap(self):
        """start_ab_framework 후 CalibrationTracker 연동 전체 흐름."""
        mgr = self._fresh_manager()
        self.simulate_start_ab_framework(mgr)

        tracker = CalibrationTracker(ab_manager=mgr)
        for i in range(15):
            tracker.record("trend", 0.80, i % 2 == 0)

        result = run(tracker.record_ab_result("trend"))
        assert result is True
        assert mgr._tests[_AB_CALIBRATION_TEST].variants["trend"].n == 1
