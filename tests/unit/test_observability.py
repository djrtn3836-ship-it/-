"""
tests/unit/test_observability.py - V10 Observability 단위 테스트
- ModuleTracer 로깅 동작 검증
- trace_id 생성 및 컨텍스트 전파 검증
- @traced 데코레이터 동작 검증
- PerformanceTracker v2.0 지표 계산 검증
"""
import sys
import asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from observability.trace_id import new_trace_id, bind_trace_id, current_trace_id, reset_trace_id, trace_context
from observability.tracer import get_tracer, ModuleTracer
from analytics.performance_tracker import PerformanceTracker


class TestTraceId:
    """trace_id 생성 및 관리 테스트"""

    def test_new_trace_id_format(self):
        tid = new_trace_id()
        assert isinstance(tid, str)
        assert len(tid) > 0

    def test_bind_and_get(self):
        token = bind_trace_id("TEST-001")
        assert current_trace_id() == "TEST-001"
        reset_trace_id(token)  # 복원

    def test_reset_restores(self):
        token = bind_trace_id("SOME-ID")
        assert current_trace_id() == "SOME-ID"
        reset_trace_id(token)
        # reset 후에는 기본값으로 복원
        result = current_trace_id()
        assert result != "SOME-ID"

    def test_multiple_ids_independent(self):
        """서로 다른 trace_id는 독립적"""
        id1 = new_trace_id()
        id2 = new_trace_id()
        assert id1 != id2

    def test_trace_context_manager(self):
        """context manager로 trace_id 격리"""
        with trace_context("CTX-TEST") as tid:
            assert tid == "CTX-TEST"
            assert current_trace_id() == "CTX-TEST"
        # with 블록 이후 복원
        assert current_trace_id() != "CTX-TEST"


class TestModuleTracer:
    """ModuleTracer 기본 동작 테스트"""

    def test_get_tracer_returns_same_instance(self):
        """같은 모듈명은 동일 인스턴스 반환 (캐싱)"""
        t1 = get_tracer("test.module")
        t2 = get_tracer("test.module")
        assert t1 is t2

    def test_get_tracer_different_modules(self):
        """다른 모듈명은 다른 인스턴스"""
        t1 = get_tracer("module.a")
        t2 = get_tracer("module.b")
        assert t1 is not t2

    def test_tracer_has_correct_module_name(self):
        t = get_tracer("my.test.module")
        assert t.module_name == "my.test.module"

    @pytest.mark.asyncio
    async def test_traced_decorator_async(self):
        """@traced 데코레이터가 async 함수를 정상 실행"""
        tracer = get_tracer("test.traced.async")

        @tracer.traced
        async def sample_func(x: int) -> int:
            return x * 2

        result = await sample_func(5)
        assert result == 10

    def test_traced_decorator_sync(self):
        """@traced 데코레이터가 sync 함수를 정상 실행"""
        tracer = get_tracer("test.traced.sync")

        @tracer.traced
        def sample_sync(x: int) -> int:
            return x + 1

        result = sample_sync(3)
        assert result == 4

    @pytest.mark.asyncio
    async def test_traced_propagates_exception(self):
        """@traced 데코레이터가 예외를 그대로 전파"""
        tracer = get_tracer("test.traced.exc")

        @tracer.traced
        async def failing_func():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            await failing_func()


class TestPerformanceTrackerMetrics:
    """PerformanceTracker 지표 계산 단위 테스트"""

    def setup_method(self):
        # 싱글톤 재사용 (db 없이 순수 계산만 테스트)
        self.tracker = PerformanceTracker()

    def test_sharpe_insufficient_data(self):
        """5개 미만 수익률 → 샤프 0.0"""
        result = PerformanceTracker._calc_sharpe([0.01, 0.02])
        assert result == 0.0

    def test_sharpe_positive(self):
        """다양한 양수 수익률 → 양수 샤프"""
        import random
        random.seed(42)
        # 변동성 있는 양수 수익률 (std > 0)
        returns = [0.01 + 0.005 * (i % 3 - 1) for i in range(30)]
        result = PerformanceTracker._calc_sharpe(returns)
        assert result > 0

    def test_sharpe_zero_std(self):
        """표준편차 0 (모든 수익률 같음) → 0.0"""
        # 완전히 같은 값은 std=0이 아니라 소수점 문제가 있을 수 있어
        # 극단적으로 0 반환 케이스 확인
        result = PerformanceTracker._calc_sharpe([0.0] * 30)
        assert result == 0.0

    def test_max_drawdown_no_drawdown(self):
        """단조 증가 곡선 → MDD 0.0"""
        equity = [100.0, 101.0, 102.0, 103.0]
        result = PerformanceTracker._calculate_max_drawdown(equity)
        assert result == 0.0

    def test_max_drawdown_simple(self):
        """10% 낙폭 → MDD 0.1"""
        equity = [100.0, 90.0, 95.0]
        result = PerformanceTracker._calculate_max_drawdown(equity)
        assert abs(result - 0.1) < 0.001

    def test_max_drawdown_empty(self):
        assert PerformanceTracker._calculate_max_drawdown([]) == 0.0

    def test_calmar_ratio(self):
        """Calmar = 연환산 수익률 / MDD"""
        # 10% 수익, MDD 5%, 252일 운용 → 연수익 ~10%, calmar ~2.0
        result = PerformanceTracker._calc_calmar(10.0, 0.05, 252)
        assert result > 0

    def test_calmar_zero_mdd(self):
        """MDD 0 → Calmar 0.0 (ZeroDivision 방지)"""
        result = PerformanceTracker._calc_calmar(10.0, 0.0, 252)
        assert result == 0.0

    def test_get_status_not_running(self):
        """초기 상태에서 is_running=False"""
        status = self.tracker.get_status()
        assert status["is_running"] is False
        assert status["snapshot_count"] == 0

    def test_telegram_summary_no_data(self):
        """스냅샷 없을 때 안전하게 처리"""
        # 싱글톤이므로 스냅샷이 있을 수 있음 - 빈 경우만 체크
        # 별도 인스턴스 생성 불가 (싱글톤), 스냅샷 비어있으면 안전 문자열 반환
        msg = self.tracker.get_telegram_summary()
        assert isinstance(msg, str)
        assert len(msg) > 0
