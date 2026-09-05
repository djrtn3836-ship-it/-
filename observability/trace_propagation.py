# -*- coding: utf-8 -*-
"""
observability/trace_propagation.py - V10 Trace ID 전파 유틸리티 v1.1
(Session 33: mypy strict 적용 — 반환 타입/제네릭 타입 명시, 로직 무변경)
"""

import asyncio
import functools
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable, Dict, Optional, TypeVar

from observability.trace_id import (
    bind_trace_id,
    current_trace_id,
    new_trace_id,
    reset_trace_id,
)

logger = logging.getLogger("trace_propagation")

_TRACE_HEADER = "X-Trace-ID"
_TRACE_FOOTER_ENABLED = True


def get_current_trace() -> str:
    return current_trace_id()


def inject_trace_id(data: Dict[str, Any], key: str = "trace_id") -> Dict[str, Any]:
    ctx_trace = current_trace_id()
    if ctx_trace and ctx_trace != "-":
        data[key] = ctx_trace
    elif key not in data:
        data[key] = new_trace_id()
    return data


def format_trace_footer(trace_id: Optional[str] = None) -> str:
    if not _TRACE_FOOTER_ENABLED:
        return ""
    tid = trace_id or current_trace_id()
    if not tid or tid == "-":
        return ""
    return f"\n🔍 <code>{tid}</code>"


@asynccontextmanager
async def propagate_trace(trace_id: Optional[str] = None) -> AsyncGenerator[str, None]:
    token = bind_trace_id(trace_id)
    try:
        yield current_trace_id()
    finally:
        reset_trace_id(token)


class TraceIdMiddleware:
    @staticmethod
    async def middleware(app: Any, handler: Callable[..., Any]) -> Callable[..., Any]:
        async def middleware_handler(request: Any) -> Any:
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
    async def handle(self, request_or_trace_id: Any = None) -> AsyncGenerator[str, None]:
        if hasattr(request_or_trace_id, "headers"):
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


F = TypeVar("F", bound=Callable[..., Any])


def with_propagated_trace(prefix: str = "TRC") -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx = current_trace_id()
            if ctx and ctx != "-":
                return await func(*args, **kwargs)
            token = bind_trace_id(new_trace_id(prefix))
            try:
                return await func(*args, **kwargs)
            finally:
                reset_trace_id(token)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
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


def enable_trace_footer(enabled: bool = True) -> None:
    global _TRACE_FOOTER_ENABLED
    _TRACE_FOOTER_ENABLED = enabled


def is_trace_footer_enabled() -> bool:
    return _TRACE_FOOTER_ENABLED
