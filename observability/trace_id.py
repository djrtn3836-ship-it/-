# -*- coding: utf-8 -*-
"""
observability/trace_id.py - V10 Trace ID Manager v1.1
(Session 33: mypy strict 적용 — 반환 타입/제네릭 타입 명시, 로직 무변경)
"""

import contextvars
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Generator, Optional

_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default="-"
)


def new_trace_id(prefix: str = "TRC") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def bind_trace_id(trace_id: Optional[str] = None) -> contextvars.Token[str]:
    if trace_id is None:
        trace_id = new_trace_id()
    return _trace_id_var.set(trace_id)


def reset_trace_id(token: contextvars.Token[str]) -> None:
    _trace_id_var.reset(token)


def current_trace_id() -> str:
    return _trace_id_var.get()


@contextmanager
def trace_context(trace_id: Optional[str] = None) -> Generator[str, None, None]:
    token = bind_trace_id(trace_id)
    try:
        yield current_trace_id()
    finally:
        reset_trace_id(token)


def with_trace_id(func: Callable[..., Any]) -> Callable[..., Any]:
    import asyncio
    import functools

    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            token = bind_trace_id()
            try:
                return await func(*args, **kwargs)
            finally:
                reset_trace_id(token)
        return async_wrapper
    else:
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            token = bind_trace_id()
            try:
                return func(*args, **kwargs)
            finally:
                reset_trace_id(token)
        return sync_wrapper
