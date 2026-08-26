# -*- coding: utf-8 -*-
"""
observability/tracer.py - V10 Unified tracing logger
- Per-module log files
- @trace.traced decorator for function entry/exit/exception logging
- Console separation (propagate=False)
"""

import functools
import inspect
import logging
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Callable, TypeVar, ParamSpec

from observability.trace_config import get_trace_manager
from observability.trace_id import current_trace_id

TRACE_DIR = Path("logs/trace")
TRACE_DIR.mkdir(parents=True, exist_ok=True)

_tracer_cache: dict[str, "ModuleTracer"] = {}

P = ParamSpec("P")
T = TypeVar("T")


class ModuleTracer:
    def __init__(self, module_name: str):
        self.module_name = module_name
        safe_name = module_name.replace("/", ".").replace("\\", ".")

        self._logger = logging.getLogger(f"trace.{module_name}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False

        if not self._logger.handlers:
            log_path = TRACE_DIR / f"{safe_name}.trace.log"
            handler = RotatingFileHandler(
                log_path,
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter(
                    "[%(asctime)s.%(msecs)03d][%(trace_id)s] %(levelname)-7s "
                    "%(filename)s:%(lineno)d %(funcName)s() | %(message)s",
                    datefmt="%H:%M:%S",
                )
            )
            self._logger.addHandler(handler)

    def _enabled(self) -> bool:
        return get_trace_manager().is_enabled(self.module_name)

    def _make_extra(self) -> dict:
        return {"trace_id": current_trace_id()}

    def debug(self, msg: str, **context) -> None:
        if not self._enabled():
            return
        if context:
            kv = " ".join(f"{k}={v!r}" for k, v in context.items())
            full_msg = f"{msg} | {kv}"
        else:
            full_msg = msg
        self._logger.debug(full_msg, extra=self._make_extra())

    def info(self, msg: str, **context) -> None:
        if not self._enabled():
            return
        if context:
            kv = " ".join(f"{k}={v!r}" for k, v in context.items())
            full_msg = f"{msg} | {kv}"
        else:
            full_msg = msg
        self._logger.info(full_msg, extra=self._make_extra())

    def warning(self, msg: str, **context) -> None:
        if not self._enabled():
            return
        if context:
            kv = " ".join(f"{k}={v!r}" for k, v in context.items())
            full_msg = f"{msg} | {kv}"
        else:
            full_msg = msg
        self._logger.warning(full_msg, extra=self._make_extra())

    def error(self, msg: str, exc: Optional[Exception] = None, **context) -> None:
        if not self._enabled():
            return
        if context:
            kv = " ".join(f"{k}={v!r}" for k, v in context.items())
            full_msg = f"{msg} | {kv}"
        else:
            full_msg = msg
        if exc:
            full_msg += f"\n--- CAUSED BY ---\n{traceback.format_exc()}"
        self._logger.error(full_msg, extra=self._make_extra())

    def traced(self, func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            if not self._enabled():
                return await func(*args, **kwargs)
            t0 = time.perf_counter()
            self.debug(f"ENTER {func.__qualname__}")
            try:
                result = await func(*args, **kwargs)
                elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
                self.debug(f"EXIT {func.__qualname__}", elapsed_ms=elapsed_ms)
                return result
            except Exception as e:
                caller = inspect.stack()[1]
                self.error(
                    f"EXCEPTION in {func.__qualname__} "
                    f"(called from {caller.filename}:{caller.lineno})",
                    exc=e,
                )
                raise

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            if not self._enabled():
                return func(*args, **kwargs)
            t0 = time.perf_counter()
            self.debug(f"ENTER {func.__qualname__}")
            try:
                result = func(*args, **kwargs)
                elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
                self.debug(f"EXIT {func.__qualname__}", elapsed_ms=elapsed_ms)
                return result
            except Exception as e:
                caller = inspect.stack()[1]
                self.error(
                    f"EXCEPTION in {func.__qualname__} "
                    f"(called from {caller.filename}:{caller.lineno})",
                    exc=e,
                )
                raise

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper


def get_tracer(module_name: str) -> ModuleTracer:
    if module_name not in _tracer_cache:
        _tracer_cache[module_name] = ModuleTracer(module_name)
    return _tracer_cache[module_name]