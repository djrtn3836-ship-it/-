# -*- coding: utf-8 -*-
"""
observability/tracer.py - V10 Unified tracing logger v1.1 (Session 32: mypy strict 적용)

v1.0 -> v1.1 변경 사항:
    - 모든 메서드에 파라미터/반환 타입 명시
    - traced() 데코레이터: inspect.iscoroutinefunction()으로 async/sync를
      런타임에 분기하여 각기 다른 래퍼를 반환하는 구조이므로, mypy의 ParamSpec은
      이 패턴에서 정확한 반환 타입을 정적으로 하나로 통일할 수 없어 [return-value]
      오류를 유발합니다. Callable[..., Any]로 선언하여 런타임 동작은 전혀 바꾸지
      않으면서 이 문제를 실용적으로 회피했습니다(@overload로 완전히 분리하는
      정확한 해법은 모든 호출부 영향 범위 검토가 필요해 별도 세션으로 미룸).
    - 로직/동작 100% 무변경
"""

import functools
import inspect
import logging
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Optional

from observability.trace_config import get_trace_manager
from observability.trace_id import current_trace_id

TRACE_DIR = Path("logs/trace")
TRACE_DIR.mkdir(parents=True, exist_ok=True)

_tracer_cache: dict[str, "ModuleTracer"] = {}


class ModuleTracer:
    def __init__(self, module_name: str) -> None:
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
        return bool(get_trace_manager().is_enabled(self.module_name))

    def _make_extra(self) -> dict[str, Any]:
        return {"trace_id": current_trace_id()}

    def debug(self, msg: str, **context: Any) -> None:
        if not self._enabled():
            return
        if context:
            kv = " ".join(f"{k}={v!r}" for k, v in context.items())
            full_msg = f"{msg} | {kv}"
        else:
            full_msg = msg
        self._logger.debug(full_msg, extra=self._make_extra())

    def info(self, msg: str, **context: Any) -> None:
        if not self._enabled():
            return
        if context:
            kv = " ".join(f"{k}={v!r}" for k, v in context.items())
            full_msg = f"{msg} | {kv}"
        else:
            full_msg = msg
        self._logger.info(full_msg, extra=self._make_extra())

    def warning(self, msg: str, **context: Any) -> None:
        if not self._enabled():
            return
        if context:
            kv = " ".join(f"{k}={v!r}" for k, v in context.items())
            full_msg = f"{msg} | {kv}"
        else:
            full_msg = msg
        self._logger.warning(full_msg, extra=self._make_extra())

    def error(self, msg: str, exc: Optional[Exception] = None, **context: Any) -> None:
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

    def traced(self, func: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
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

            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
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

            return sync_wrapper


def get_tracer(module_name: str) -> ModuleTracer:
    if module_name not in _tracer_cache:
        _tracer_cache[module_name] = ModuleTracer(module_name)
    return _tracer_cache[module_name]
