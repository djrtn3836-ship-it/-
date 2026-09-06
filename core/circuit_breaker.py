# -*- coding: utf-8 -*-
"""
core/circuit_breaker.py - v5.1.3 (Session 35: mypy strict 적용, 로직 무변경)

주의: protect() 데코레이터는 원본부터 항상 async_wrapper를 반환합니다
(동기 함수를 감싸도 호출 시 코루틴을 반환하는 기존 동작을 그대로 보존).
"""

import asyncio
import functools
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional, TypeVar, cast

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CBConfig:
    failure_threshold: int = 3
    timeout: float = 30.0
    half_open_max_calls: int = 3

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if self.timeout < 1.0:
            raise ValueError("timeout must be >= 1.0")
        if self.half_open_max_calls < 1:
            raise ValueError("half_open_max_calls must be >= 1")


CIRCUIT_BREAKER_CONFIGS: Dict[str, CBConfig] = {
    "kiwoom_tr": CBConfig(failure_threshold=3, timeout=30.0, half_open_max_calls=2),
    "kiwoom_realtime": CBConfig(failure_threshold=5, timeout=60.0, half_open_max_calls=3),
    "dart_api": CBConfig(failure_threshold=3, timeout=120.0, half_open_max_calls=2),
    "news_crawler": CBConfig(failure_threshold=3, timeout=60.0, half_open_max_calls=3),
    "default": CBConfig(failure_threshold=3, timeout=30.0, half_open_max_calls=3),
}

F = TypeVar("F", bound=Callable[..., Any])


class CircuitBreaker:
    def __init__(self, name: str, config: Optional[CBConfig] = None) -> None:
        self.name = name
        self.config = config or CIRCUIT_BREAKER_CONFIGS.get(name, CIRCUIT_BREAKER_CONFIGS["default"])
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: float = 0.0
        self.half_open_calls = 0
        self._last_error: Optional[Exception] = None
        self._total_failures = 0
        self._total_successes = 0

    def protect(self, func: F) -> F:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if self.state == CircuitState.OPEN:
                elapsed = time.time() - self.last_failure_time
                if elapsed > self.config.timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                    logger.info(f"[{self.name}] Half-Open 전환")
                else:
                    logger.warning(f"[{self.name}] OPEN 차단 (잔여: {self.config.timeout - elapsed:.1f}s)")
                    return None

            if self.state == CircuitState.HALF_OPEN:
                self.half_open_calls += 1
                if self.half_open_calls > self.config.half_open_max_calls:
                    logger.warning(f"[{self.name}] Half-Open 호출 초과")
                    return None

            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)

                self._total_successes += 1
                self.failure_count = 0
                if self.state == CircuitState.HALF_OPEN:
                    self.state = CircuitState.CLOSED
                    logger.info(f"[{self.name}] CLOSED 복원")
                return result

            except Exception as e:
                self._last_error = e
                self._total_failures += 1
                self.failure_count += 1
                self.last_failure_time = time.time()

                if self.failure_count >= self.config.failure_threshold:
                    self.state = CircuitState.OPEN
                    logger.critical(
                        f"[{self.name}] OPEN 전환 (실패 {self.failure_count}/{self.config.failure_threshold})"
                    )
                else:
                    logger.warning(f"[{self.name}] 실패 ({self.failure_count}/{self.config.failure_threshold})")
                raise

        return cast(F, async_wrapper)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "threshold": self.config.failure_threshold,
            "timeout": self.config.timeout,
            "total_successes": self._total_successes,
            "total_failures": self._total_failures,
            "last_error": str(self._last_error) if self._last_error else None,
            "is_healthy": self.state == CircuitState.CLOSED,
        }


KIWOOM_TR_CB = CircuitBreaker("kiwoom_tr")
KIWOOM_REALTIME_CB = CircuitBreaker("kiwoom_realtime")
DART_API_CB = CircuitBreaker("dart_api")
NEWS_CRAWLER_CB = CircuitBreaker("news_crawler")
