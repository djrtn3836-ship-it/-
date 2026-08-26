# -*- coding: utf-8 -*-
"""
observability/trace_id.py - V10 Trace ID Manager (Contextvars based)
- Trace ID propagation across async tasks
- Binding and resetting for worker loops
"""

import contextvars
import uuid
from typing import Optional

_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default="-"
)


def new_trace_id(prefix: str = "TRC") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def bind_trace_id(trace_id: Optional[str] = None) -> contextvars.Token:
    if trace_id is None:
        trace_id = new_trace_id()
    return _trace_id_var.set(trace_id)


def reset_trace_id(token: contextvars.Token) -> None:
    _trace_id_var.reset(token)


def current_trace_id() -> str:
    return _trace_id_var.get()


from contextlib import contextmanager

@contextmanager
def trace_context(trace_id: Optional[str] = None):
    token = bind_trace_id(trace_id)
    try:
        yield current_trace_id()
    finally:
        reset_trace_id(token)


def with_trace_id(func):
    import asyncio
    import functools

    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            token = bind_trace_id()
            try:
                return await func(*args, **kwargs)
            finally:
                reset_trace_id(token)
        return async_wrapper
    else:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            token = bind_trace_id()
            try:
                return func(*args, **kwargs)
            finally:
                reset_trace_id(token)
        return sync_wrapper