# -*- coding: utf-8 -*-
"""
observability/trace_propagation.py - V10 Trace ID 전파 유틸리티 v1.0

Trace ID 전파 체인:
    HTTP 요청 → bind_trace_id(X-Trace-ID 헤더) → 처리 로직
              → DB 저장 (decisions.trace_id 컬럼 자동 포함)
              → Telegram 알림 (메시지 푸터에 trace_id 포함)

주요 컴포넌트:
    - TraceIdMiddleware: aiohttp/웹서버 미들웨어용 trace_id 자동 바인딩
    - inject_trace_id: 딕셔너리에 trace_id 자동 주입 (DB 저장용)
    - format_trace_footer: Telegram 메시지 푸터에 trace_id 표시
    - propagate_trace: async 컨텍스트 매니저 (trace_id 범위 지정)
    - get_current_trace: 현재 trace_id 반환 (편의 함수)
"""

import asyncio
import functools
import logging
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Optional, TypeVar

from observability.trace_id import (
    bind_trace_id,
    current_trace_id,
    new_trace_id,
    reset_trace_id,
)

logger = logging.getLogger("trace_propagation")

_TRACE_HEADER = "X-Trace-ID"         # HTTP 헤더 이름
_TRACE_FOOTER_ENABLED = True         # Telegram 푸터 표시 여부 (테스트에서 끌 수 있음)


# ═══════════════════════════════════════════════════════════════════
#  편의 함수
# ═══════════════════════════════════════════════════════════════════

def get_current_trace() -> str:
    """현재 컨텍스트의 trace_id 반환.

    Returns:
        str: 현재 trace_id ("-"는 미설정 상태)
    """
    return current_trace_id()


def inject_trace_id(data: Dict[str, Any], key: str = "trace_id") -> Dict[str, Any]:
    """딕셔너리에 현재 trace_id를 자동 주입 (DB 저장용).

    data에 이미 trace_id가 있으면 덮어쓰지 않고 현재 컨텍스트 값을
    우선합니다 (컨텍스트가 "-"이면 기존 값 유지).

    Args:
        data: trace_id를 주입할 딕셔너리
        key: trace_id 키 이름 (기본: "trace_id")

    Returns:
        Dict[str, Any]: trace_id가 주입된 딕셔너리 (원본 수정)
    """
    ctx_trace = current_trace_id()
    if ctx_trace and ctx_trace != "-":
        data[key] = ctx_trace
    elif key not in data:
        data[key] = new_trace_id()  # fallback: 새 ID 생성
    return data


def format_trace_footer(trace_id: Optional[str] = None) -> str:
    """Telegram 알림 메시지 푸터용 trace_id 문자열 생성.

    Args:
        trace_id: 명시적 trace_id (None이면 현재 컨텍스트 사용)

    Returns:
        str: 푸터 문자열 (예: "\n🔍 <code>TRC-abc12345</code>")
             _TRACE_FOOTER_ENABLED=False이면 빈 문자열
    """
    if not _TRACE_FOOTER_ENABLED:
        return ""
    tid = trace_id or current_trace_id()
    if not tid or tid == "-":
        return ""
    return f"\n🔍 <code>{tid}</code>"


# ═══════════════════════════════════════════════════════════════════
#  컨텍스트 매니저
# ═══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def propagate_trace(trace_id: Optional[str] = None):
    """async 컨텍스트 매니저 — trace_id 전파 범위 지정.

    Usage:
        async with propagate_trace("MY-TRACE-001") as tid:
            await some_db_operation()
            await some_telegram_send()
            # 위 작업에서 current_trace_id() == "MY-TRACE-001"

    Args:
        trace_id: 사용할 trace_id (None이면 새로 생성)

    Yields:
        str: 현재 trace_id
    """
    token = bind_trace_id(trace_id)
    try:
        yield current_trace_id()
    finally:
        reset_trace_id(token)


# ═══════════════════════════════════════════════════════════════════
#  HTTP 미들웨어 (aiohttp 호환)
# ═══════════════════════════════════════════════════════════════════

class TraceIdMiddleware:
    """aiohttp 미들웨어 — HTTP 요청에서 trace_id 자동 바인딩.

    요청 처리 순서:
        1. X-Trace-ID 헤더에서 trace_id 추출
        2. 없으면 새 trace_id 생성 (TRC-{hex8})
        3. contextvars에 바인딩
        4. 응답 헤더에 X-Trace-ID 포함
        5. 요청 처리 완료 후 trace_id 리셋

    Usage (aiohttp):
        app = web.Application(middlewares=[TraceIdMiddleware.middleware])

    Usage (직접 사용):
        middleware = TraceIdMiddleware()
        async with middleware.handle(request) as tid:
            response = await handler(request)
    """

    @staticmethod
    async def middleware(app, handler):
        """aiohttp 미들웨어 팩토리."""
        async def middleware_handler(request):
            trace_id = request.headers.get(_TRACE_HEADER) or new_trace_id("REQ")
            token = bind_trace_id(trace_id)
            try:
                response = await handler(request)
                response.headers[_TRACE_HEADER] = trace_id
                return response
            finally:
                reset_trace_id(token)
        return middleware_handler

    @asynccontextmanager
    async def handle(self, request_or_trace_id=None):
        """범용 trace_id 핸들러.

        Args:
            request_or_trace_id: aiohttp Request 객체 또는 trace_id 문자열

        Yields:
            str: 현재 trace_id
        """
        if hasattr(request_or_trace_id, "headers"):
            # aiohttp Request
            trace_id = request_or_trace_id.headers.get(_TRACE_HEADER) or new_trace_id("REQ")
        elif isinstance(request_or_trace_id, str):
            trace_id = request_or_trace_id
        else:
            trace_id = new_trace_id("REQ")

        token = bind_trace_id(trace_id)
        try:
            yield current_trace_id()
        finally:
            reset_trace_id(token)


# ═══════════════════════════════════════════════════════════════════
#  데코레이터 — trace_id 자동 바인딩
# ═══════════════════════════════════════════════════════════════════

F = TypeVar("F", bound=Callable)


def with_propagated_trace(prefix: str = "TRC") -> Callable[[F], F]:
    """async 함수에 trace_id 자동 바인딩 데코레이터.

    이미 trace_id가 설정된 경우 기존 값 유지.
    설정되지 않은 경우 새 trace_id 생성 후 함수 실행 범위에 바인딩.

    Args:
        prefix: 새 trace_id 생성 시 접두사 (기본: "TRC")

    Usage:
        @with_propagated_trace("HTTP")
        async def handle_request(data: dict):
            ...  # current_trace_id()가 자동으로 설정됨
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            ctx = current_trace_id()
            if ctx and ctx != "-":
                # 이미 trace_id 있음 → 그대로 실행
                return await func(*args, **kwargs)
            # 새 trace_id 생성
            token = bind_trace_id(new_trace_id(prefix))
            try:
                return await func(*args, **kwargs)
            finally:
                reset_trace_id(token)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            ctx = current_trace_id()
            if ctx and ctx != "-":
                return func(*args, **kwargs)
            token = bind_trace_id(new_trace_id(prefix))
            try:
                return func(*args, **kwargs)
            finally:
                reset_trace_id(token)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper  # type: ignore[return-value]

    return decorator


# ═══════════════════════════════════════════════════════════════════
#  전역 설정
# ═══════════════════════════════════════════════════════════════════

def enable_trace_footer(enabled: bool = True) -> None:
    """Telegram 알림 trace_id 푸터 표시 ON/OFF.

    Args:
        enabled: True=표시, False=숨김
    """
    global _TRACE_FOOTER_ENABLED
    _TRACE_FOOTER_ENABLED = enabled


def is_trace_footer_enabled() -> bool:
    """현재 Telegram trace_id 푸터 표시 여부."""
    return _TRACE_FOOTER_ENABLED
