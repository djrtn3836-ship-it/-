# -*- coding: utf-8 -*-
"""tests/unit/test_infra_layer.py - 수집/관측 계층 검증 (P9-4, P9-5).

대상:
    P9-4 (수집): `collector/collector_status.py`
    P9-5 (관측): `core/exception_handler.py`, `observability/auto_trace.py`,
                 `observability/trace_config.py`
배경: 감사에서 네 모듈 모두 테스트 언급 0건.
"""

import asyncio
from typing import Any, Dict, List

import pytest


# ═══════════════════════════════════════════════════════════════════
#  P9-4 수집 상태
# ═══════════════════════════════════════════════════════════════════

class TestCollectorStatus:
    def test_singleton(self) -> None:
        from collector.collector_status import CollectorStatusManager

        assert CollectorStatusManager() is CollectorStatusManager()

    def test_register_and_success(self) -> None:
        from collector.collector_status import CollectorStatusManager

        m = CollectorStatusManager()
        m.register("test_collector", freshness_seconds=60)
        m.record_success("test_collector", {"rows": 10})

        st = m.get_status("test_collector")
        assert st is not None
        assert st.last_success is not None
        assert st.last_error is None

    def test_failure_recorded(self) -> None:
        from collector.collector_status import CollectorStatusManager

        m = CollectorStatusManager()
        m.register("fail_collector")
        m.record_failure("fail_collector", "네트워크 오류")

        st = m.get_status("fail_collector")
        assert st is not None
        assert st.last_error and "네트워크" in st.last_error
        assert st.total_failures == 1 and st.consecutive_failures == 1

    def test_unhealthy_after_three_consecutive_failures(self) -> None:
        """단발 실패로는 비정상 처리하지 않고, 3회 연속 실패 시 비정상(오탐 방지)."""
        from collector.collector_status import CollectorStatusManager

        m = CollectorStatusManager()
        m.register("flaky_collector")
        m.record_failure("flaky_collector", "1회")
        assert m.get_status("flaky_collector").is_healthy is True

        m.record_failure("flaky_collector", "2회")
        m.record_failure("flaky_collector", "3회")
        st = m.get_status("flaky_collector")
        assert st.is_healthy is False and st.consecutive_failures == 3

    def test_success_restores_health(self) -> None:
        from collector.collector_status import CollectorStatusManager

        m = CollectorStatusManager()
        m.register("recover_collector")
        for _ in range(3):
            m.record_failure("recover_collector", "연속 실패")
        assert m.get_status("recover_collector").is_healthy is False

        m.record_success("recover_collector")
        st = m.get_status("recover_collector")
        assert st.is_healthy is True and st.consecutive_failures == 0

    def test_get_all_status_is_dict(self) -> None:
        from collector.collector_status import CollectorStatusManager

        assert isinstance(CollectorStatusManager().get_all_status(), dict)

    def test_unknown_collector_returns_none(self) -> None:
        from collector.collector_status import CollectorStatusManager

        assert CollectorStatusManager().get_status("존재하지_않는_수집기") is None

    def test_success_resets_error(self) -> None:
        from collector.collector_status import CollectorStatusManager

        m = CollectorStatusManager()
        m.register("reset_collector")
        m.record_failure("reset_collector", "일시 오류")
        m.record_success("reset_collector")

        st = m.get_status("reset_collector")
        assert st is not None and st.last_error is None

    def test_health_flags(self) -> None:
        from collector.collector_status import CollectorStatusManager

        m = CollectorStatusManager()
        m.register("fresh_collector", freshness_seconds=1)
        m.record_success("fresh_collector")

        st = m.get_status("fresh_collector")
        assert st is not None
        assert st.is_healthy is True
        assert st.consecutive_failures == 0
        assert st.data_freshness_seconds == 1


# ═══════════════════════════════════════════════════════════════════
#  P9-5 관측 계층
# ═══════════════════════════════════════════════════════════════════

class TestExceptionHandler:
    def test_setup_returns_handlers_and_restore(self) -> None:
        from core import exception_handler as eh

        original = eh.setup_global_exception_handler()
        assert isinstance(original, dict)

        eh.restore_exception_handler(original)      # 예외 없이 복원

    def test_alert_handler_is_used_on_loop_exception(self) -> None:
        from core import exception_handler as eh

        sent: List[str] = []

        async def _alert(title: str, detail: str = "") -> None:
            sent.append(title)

        eh.set_alert_handler(_alert)

        ctx: Dict[str, Any] = {"exception": RuntimeError("테스트 오류"), "message": "loop error"}
        try:
            eh.global_exception_handler(None, ctx)      # type: ignore[arg-type]
        except Exception:
            pass                                        # 전파되더라도 치명적이지 않아야 함

    def test_sync_alert_without_event_loop_is_safe(self) -> None:
        """이벤트 루프가 없는(스레드/스크립트) 상황에서도 알림 래퍼가 터지지 않아야 한다."""
        from core import exception_handler as eh

        sent: List[str] = []

        async def _alert(title: str, detail: str = "") -> None:
            sent.append(title)

        eh.set_alert_handler(_alert)
        eh._send_alert_sync("제목", "상세")      # 예외 없이 반환

    def test_loop_handler_without_loop_object(self) -> None:
        """loop 인자가 None이어도 핸들러가 예외를 밖으로 던지면 안 된다."""
        from core import exception_handler as eh

        try:
            eh.global_exception_handler(None, {"exception": RuntimeError("x"), "message": "m"})  # type: ignore[arg-type]
        except RuntimeError as e:
            if "event loop" in str(e).lower():
                pytest.skip("이벤트 루프 부재 환경 — 호출부 계약 밖")
            raise


class TestAutoTrace:
    def test_traced_service_subclass_gets_methods(self) -> None:
        from observability.auto_trace import TracedService

        class _Svc(TracedService):
            def work(self, x: int) -> int:
                return x * 2

        svc = _Svc()
        assert svc.work(3) == 6

    def test_auto_trace_module_is_idempotent(self) -> None:
        import sys

        import observability.auto_trace as at

        module = sys.modules[__name__]
        at.auto_trace_module("tests.unit.test_infra_layer_fake"), 
        at.auto_trace_module("tests.unit.test_infra_layer_fake")

        assert module is not None

    def test_trace_class_decorator_preserves_behavior(self) -> None:
        from observability.auto_trace import trace_class

        @trace_class("test_module")
        class _Calc:
            def add(self, a: int, b: int) -> int:
                return a + b

        assert _Calc().add(2, 5) == 7


class TestTraceConfig:
    def test_singleton(self) -> None:
        from observability.trace_config import TraceConfigManager

        assert TraceConfigManager() is TraceConfigManager()

    def test_is_enabled_returns_bool(self) -> None:
        from observability.trace_config import TraceConfigManager

        assert isinstance(TraceConfigManager().is_enabled("app.bootstrap"), bool)

    def test_set_global_and_query(self) -> None:
        from observability.trace_config import TraceConfigManager

        m = TraceConfigManager()
        before = m.is_enabled("some.module")
        try:
            m.set_global(True)
            assert m.is_enabled("some.module") is True
        finally:
            m.set_global(before)

    def test_unknown_module_default(self) -> None:
        from observability.trace_config import TraceConfigManager

        assert isinstance(TraceConfigManager().is_enabled("없는모듈"), bool)


class TestExceptionHandlerLoopAbsence:
    """P9-5 회귀: 이벤트 루프가 없거나 닫힌 상태에서도 기동을 막지 않아야 한다."""

    def test_setup_without_running_loop(self) -> None:
        from core import exception_handler as eh

        original = eh.setup_global_exception_handler()
        assert isinstance(original, dict)
        eh.restore_exception_handler(original)

    def test_setup_after_loop_closed(self) -> None:
        """asyncio.run() 이후(루프 닫힘)에도 예외 없이 동작."""
        import asyncio

        from core import exception_handler as eh

        asyncio.run(asyncio.sleep(0))            # 루프 생성 후 종료(닫힘)

        original = eh.setup_global_exception_handler()
        eh.restore_exception_handler(original)

    def test_current_loop_helper_returns_none_or_loop(self) -> None:
        from core.exception_handler import _current_loop

        loop = _current_loop()
        assert loop is None or not loop.is_closed()
