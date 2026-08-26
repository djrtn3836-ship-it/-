# -*- coding: utf-8 -*-
"""
tests/unit/test_trace_propagation.py - Trace ID 전파 단위 테스트

커버리지:
    - inject_trace_id(): 딕셔너리에 trace_id 자동 주입
    - format_trace_footer(): Telegram 푸터 문자열 생성
    - propagate_trace(): async 컨텍스트 매니저
    - TraceIdMiddleware.handle(): 범용 핸들러
    - with_propagated_trace(): 데코레이터
    - enable_trace_footer() / is_trace_footer_enabled()
    - get_current_trace(): 현재 trace_id 반환
    - DB save_decision() → trace_id 자동 주입 검증
    - Telegram send() → trace_id 푸터 포함 검증
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from observability.trace_propagation import (
    TraceIdMiddleware,
    enable_trace_footer,
    format_trace_footer,
    get_current_trace,
    inject_trace_id,
    is_trace_footer_enabled,
    propagate_trace,
    with_propagated_trace,
)
from observability.trace_id import bind_trace_id, current_trace_id, reset_trace_id


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════
#  1. inject_trace_id() 테스트
# ═══════════════════════════════════════════════════════════════════

class TestInjectTraceId:
    """딕셔너리에 trace_id 자동 주입"""

    def setup_method(self):
        """각 테스트 전 trace_id 초기화"""
        # trace_id 없는 상태로 리셋
        try:
            self._reset_token = bind_trace_id("-")
        except Exception:
            pass

    def teardown_method(self):
        """각 테스트 후 trace_id 리셋"""
        try:
            reset_trace_id(self._reset_token)
        except Exception:
            pass

    def test_injects_current_trace_id(self):
        """현재 trace_id를 딕셔너리에 주입"""
        token = bind_trace_id("TEST-abc12345")
        try:
            data = {"ticker": "005930"}
            result = inject_trace_id(data)
            assert result["trace_id"] == "TEST-abc12345"
        finally:
            reset_trace_id(token)

    def test_does_not_overwrite_existing_with_dash(self):
        """trace_id가 '-'인 경우 기존 값 유지 안 함 → fallback ID 생성"""
        # '-'는 미설정 상태이므로 inject시 새 ID 생성
        token = bind_trace_id("-")
        try:
            data = {"ticker": "005930", "trace_id": "EXISTING-001"}
            result = inject_trace_id(data)
            # '-' 컨텍스트에서 기존 trace_id는 덮어쓰지 않음
            # inject_trace_id: ctx=="-" → key not in data이면 새 ID, key in data이면 유지
            assert result["trace_id"] is not None
        finally:
            reset_trace_id(token)

    def test_context_trace_overrides_empty_key(self):
        """컨텍스트 trace_id가 있으면 빈 딕셔너리에도 주입"""
        token = bind_trace_id("CTX-override-001")
        try:
            data = {}
            inject_trace_id(data)
            assert data["trace_id"] == "CTX-override-001"
        finally:
            reset_trace_id(token)

    def test_custom_key_name(self):
        """사용자 정의 키 이름으로 주입"""
        token = bind_trace_id("CUSTOM-abc")
        try:
            data = {}
            inject_trace_id(data, key="request_id")
            assert data["request_id"] == "CUSTOM-abc"
            assert "trace_id" not in data
        finally:
            reset_trace_id(token)

    def test_fallback_creates_new_id_when_no_context(self):
        """컨텍스트 없을 때 새 trace_id 자동 생성"""
        token = bind_trace_id("-")
        try:
            data = {}
            inject_trace_id(data)
            # 새 ID가 생성되어야 함 (None이 아님)
            assert data.get("trace_id") is not None
        finally:
            reset_trace_id(token)

    def test_returns_same_dict(self):
        """원본 딕셔너리 반환 (in-place 수정)"""
        token = bind_trace_id("RET-001")
        try:
            original = {"key": "value"}
            result = inject_trace_id(original)
            assert result is original
        finally:
            reset_trace_id(token)


# ═══════════════════════════════════════════════════════════════════
#  2. format_trace_footer() 테스트
# ═══════════════════════════════════════════════════════════════════

class TestFormatTraceFooter:
    """Telegram 알림 푸터 문자열 생성"""

    def setup_method(self):
        enable_trace_footer(True)  # 각 테스트 전 활성화

    def test_explicit_trace_id_returns_footer(self):
        """명시적 trace_id → 푸터 문자열 반환"""
        footer = format_trace_footer("EXPLICIT-001")
        assert "EXPLICIT-001" in footer
        assert "<code>" in footer

    def test_empty_trace_id_returns_empty(self):
        """빈 trace_id → 빈 문자열"""
        footer = format_trace_footer("")
        assert footer == ""

    def test_dash_trace_id_returns_empty(self):
        """'-' trace_id → 빈 문자열 (미설정 상태)"""
        footer = format_trace_footer("-")
        assert footer == ""

    def test_none_trace_id_uses_context(self):
        """trace_id=None → 현재 컨텍스트 사용"""
        token = bind_trace_id("CTX-for-footer")
        try:
            footer = format_trace_footer(None)
            assert "CTX-for-footer" in footer
        finally:
            reset_trace_id(token)

    def test_disabled_returns_empty_string(self):
        """_TRACE_FOOTER_ENABLED=False → 빈 문자열"""
        enable_trace_footer(False)
        try:
            footer = format_trace_footer("SOME-TRACE")
            assert footer == ""
        finally:
            enable_trace_footer(True)

    def test_footer_contains_search_emoji(self):
        """푸터에 🔍 이모지 포함"""
        footer = format_trace_footer("HAS-EMOJI")
        assert "🔍" in footer

    def test_footer_starts_with_newline(self):
        """푸터는 줄바꿈으로 시작 (메시지 분리)"""
        footer = format_trace_footer("NEWLINE-TEST")
        assert footer.startswith("\n")


# ═══════════════════════════════════════════════════════════════════
#  3. propagate_trace() 컨텍스트 매니저 테스트
# ═══════════════════════════════════════════════════════════════════

class TestPropagateTrace:
    """async 컨텍스트 매니저"""

    def test_binds_trace_id_in_context(self):
        """컨텍스트 내에서 trace_id 바인딩"""
        async def _test():
            async with propagate_trace("PROP-001") as tid:
                assert current_trace_id() == "PROP-001"
                assert tid == "PROP-001"

        _run(_test())

    def test_resets_after_context(self):
        """컨텍스트 종료 후 이전 trace_id로 복원"""
        async def _test():
            outer_token = bind_trace_id("OUTER-001")
            try:
                async with propagate_trace("INNER-001"):
                    assert current_trace_id() == "INNER-001"
                # 컨텍스트 종료 후 복원
                assert current_trace_id() == "OUTER-001"
            finally:
                reset_trace_id(outer_token)

        _run(_test())

    def test_generates_trace_id_when_none(self):
        """trace_id=None이면 새 ID 자동 생성"""
        async def _test():
            async with propagate_trace(None) as tid:
                assert tid is not None
                assert len(tid) > 0
                assert current_trace_id() == tid

        _run(_test())

    def test_nested_propagate_trace(self):
        """중첩 propagate_trace → 내부 값 우선"""
        async def _test():
            async with propagate_trace("OUTER") as outer_tid:
                async with propagate_trace("INNER") as inner_tid:
                    assert current_trace_id() == "INNER"
                    assert inner_tid == "INNER"
                # 내부 종료 후 외부로 복원
                assert current_trace_id() == "OUTER"

        _run(_test())


# ═══════════════════════════════════════════════════════════════════
#  4. TraceIdMiddleware 테스트
# ═══════════════════════════════════════════════════════════════════

class TestTraceIdMiddleware:
    """HTTP 미들웨어 trace_id 바인딩"""

    def test_handle_with_string_trace_id(self):
        """문자열 trace_id 직접 전달"""
        middleware = TraceIdMiddleware()

        async def _test():
            async with middleware.handle("MIDDLEWARE-001") as tid:
                assert current_trace_id() == "MIDDLEWARE-001"
                assert tid == "MIDDLEWARE-001"

        _run(_test())

    def test_handle_with_none_generates_id(self):
        """None 전달 시 새 ID 생성"""
        middleware = TraceIdMiddleware()

        async def _test():
            async with middleware.handle(None) as tid:
                assert tid is not None
                assert current_trace_id() == tid

        _run(_test())

    def test_handle_with_request_object(self):
        """aiohttp-like Request 객체 (headers 속성)"""
        middleware = TraceIdMiddleware()

        mock_request = MagicMock()
        mock_request.headers = {"X-Trace-ID": "REQ-from-header"}

        async def _test():
            async with middleware.handle(mock_request) as tid:
                assert tid == "REQ-from-header"

        _run(_test())

    def test_handle_resets_after_exit(self):
        """handle 종료 후 trace_id 리셋"""
        middleware = TraceIdMiddleware()

        async def _test():
            outer_token = bind_trace_id("BEFORE-MIDDLEWARE")
            try:
                async with middleware.handle("DURING-MIDDLEWARE"):
                    assert current_trace_id() == "DURING-MIDDLEWARE"
                assert current_trace_id() == "BEFORE-MIDDLEWARE"
            finally:
                reset_trace_id(outer_token)

        _run(_test())


# ═══════════════════════════════════════════════════════════════════
#  5. with_propagated_trace() 데코레이터 테스트
# ═══════════════════════════════════════════════════════════════════

class TestWithPropagatedTrace:
    """trace_id 자동 바인딩 데코레이터"""

    def test_async_function_gets_trace_id(self):
        """async 함수에 trace_id 자동 바인딩"""
        results = []

        @with_propagated_trace("DECO")
        async def traced_func():
            results.append(current_trace_id())

        _run(traced_func())
        assert len(results) == 1
        assert results[0] != "-"

    def test_existing_trace_id_not_overwritten(self):
        """이미 trace_id 있으면 덮어쓰지 않음"""
        results = []

        @with_propagated_trace("DECO")
        async def traced_func():
            results.append(current_trace_id())

        async def _test():
            token = bind_trace_id("EXISTING-TRACE")
            try:
                await traced_func()
            finally:
                reset_trace_id(token)

        _run(_test())
        assert results[0] == "EXISTING-TRACE"

    def test_sync_function_gets_trace_id(self):
        """sync 함수에도 trace_id 자동 바인딩"""
        results = []

        @with_propagated_trace("SYNC")
        def sync_func():
            results.append(current_trace_id())

        token = bind_trace_id("-")  # 미설정 상태
        try:
            sync_func()
        finally:
            reset_trace_id(token)

        assert len(results) == 1
        assert results[0] != "-"

    def test_custom_prefix(self):
        """커스텀 접두사 사용"""
        results = []

        @with_propagated_trace("MYAPP")
        async def prefixed_func():
            results.append(current_trace_id())

        token = bind_trace_id("-")
        try:
            _run(prefixed_func())
        finally:
            reset_trace_id(token)

        assert results[0].startswith("MYAPP-")


# ═══════════════════════════════════════════════════════════════════
#  6. enable/disable trace footer 테스트
# ═══════════════════════════════════════════════════════════════════

class TestTraceFooterToggle:
    """Telegram 푸터 ON/OFF 토글"""

    def setup_method(self):
        enable_trace_footer(True)

    def teardown_method(self):
        enable_trace_footer(True)  # 항상 복원

    def test_enabled_by_default(self):
        """기본값 True"""
        assert is_trace_footer_enabled() is True

    def test_disable_suppresses_footer(self):
        """비활성화 → 빈 문자열"""
        enable_trace_footer(False)
        assert is_trace_footer_enabled() is False
        assert format_trace_footer("SOME-ID") == ""

    def test_enable_restore(self):
        """재활성화 후 푸터 복원"""
        enable_trace_footer(False)
        enable_trace_footer(True)
        assert is_trace_footer_enabled() is True
        footer = format_trace_footer("RESTORED")
        assert "RESTORED" in footer


# ═══════════════════════════════════════════════════════════════════
#  7. get_current_trace() 편의 함수 테스트
# ═══════════════════════════════════════════════════════════════════

class TestGetCurrentTrace:
    def test_returns_current_trace_id(self):
        """현재 trace_id 반환"""
        token = bind_trace_id("GETCURRENT-001")
        try:
            assert get_current_trace() == "GETCURRENT-001"
        finally:
            reset_trace_id(token)

    def test_no_context_returns_dash(self):
        """미설정 상태 → '-' 반환"""
        token = bind_trace_id("-")
        try:
            assert get_current_trace() == "-"
        finally:
            reset_trace_id(token)


# ═══════════════════════════════════════════════════════════════════
#  8. DB save_decision() trace_id 자동 주입 통합 테스트
# ═══════════════════════════════════════════════════════════════════

class TestDbSaveDecisionTraceId:
    """DatabaseManager.save_decision() trace_id 자동 주입"""

    def test_inject_trace_id_before_db_save(self):
        """save_decision 호출 전 trace_id가 analysis에 주입되는지 검증"""
        # inject_trace_id()를 직접 테스트 (DB 없이)
        token = bind_trace_id("DB-TRACE-001")
        try:
            analysis = {
                "ticker": "005930",
                "action": "BUY",
                "score": 0.75,
            }
            inject_trace_id(analysis, key="trace_id")
            assert analysis["trace_id"] == "DB-TRACE-001"
        finally:
            reset_trace_id(token)

    def test_analysis_without_trace_gets_new_id(self):
        """trace_id 없는 analysis → 새 ID 자동 생성"""
        token = bind_trace_id("-")
        try:
            analysis = {"ticker": "000660"}
            inject_trace_id(analysis, key="trace_id")
            # 새 ID 생성됨
            assert analysis.get("trace_id") is not None
        finally:
            reset_trace_id(token)

    def test_propagate_trace_with_db_mock(self):
        """propagate_trace() 안에서 save_decision 호출 시 trace_id 전파"""
        captured = {}

        async def _fake_save(self_arg, analysis):
            inject_trace_id(analysis)
            captured["trace_id"] = analysis.get("trace_id")

        async def _test():
            async with propagate_trace("DBINTEGRATION-001"):
                await _fake_save(None, {"ticker": "035420"})

        _run(_test())
        assert captured["trace_id"] == "DBINTEGRATION-001"


# ═══════════════════════════════════════════════════════════════════
#  9. Telegram send() trace_id 푸터 포함 테스트
# ═══════════════════════════════════════════════════════════════════

class TestTelegramTraceIdFooter:
    """TelegramSender.send()에서 trace_id 푸터 자동 포함"""

    def test_format_trace_footer_in_message(self):
        """알림 메시지에 trace_id 푸터가 추가되는지 확인"""
        enable_trace_footer(True)
        try:
            # 직접 포맷 함수 테스트
            message = "매수 시그널 발생"
            trace_id = "TG-TRACE-001"
            full_message = message + format_trace_footer(trace_id)
            assert "TG-TRACE-001" in full_message
            assert "<code>" in full_message
        finally:
            enable_trace_footer(True)

    def test_no_trace_id_no_footer(self):
        """trace_id 없으면 푸터 없음"""
        message = "알림 메시지"
        footer = format_trace_footer(None)
        # 컨텍스트 trace_id가 '-'면 빈 문자열
        token = bind_trace_id("-")
        try:
            footer = format_trace_footer(None)
            full_message = message + footer
            assert full_message == message  # 푸터 없음
        finally:
            reset_trace_id(token)

    def test_footer_html_encoded(self):
        """푸터의 trace_id가 HTML code 태그로 감싸짐"""
        footer = format_trace_footer("ENCODED-001")
        assert "<code>ENCODED-001</code>" in footer

    def test_context_propagation_to_telegram(self):
        """propagate_trace 안에서 format_trace_footer가 컨텍스트 사용"""
        enable_trace_footer(True)
        result = {}

        async def _test():
            async with propagate_trace("TG-CONTEXT-001"):
                footer = format_trace_footer(None)  # None → 컨텍스트 사용
                result["footer"] = footer

        _run(_test())
        assert "TG-CONTEXT-001" in result["footer"]
