# -*- coding: utf-8 -*-
"""
collector/collector_status.py - v1.1 (Session 32: mypy strict 적용)
- 전체 메서드 반환 타입/제네릭 타입 명시, 로직/동작 100% 무변경
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CollectorStatus:
    """개별 수집기 상태"""

    name: str
    last_success: datetime | None = None
    last_attempt: datetime | None = None
    consecutive_failures: int = 0
    total_success: int = 0
    total_failures: int = 0
    last_error: str | None = None
    is_healthy: bool = True
    data_freshness_seconds: int | None = None
    last_data: dict[str, Any] | None = None


class CollectorStatusManager:
    """수집기 상태 관리자 (싱글톤)"""

    _instance: "CollectorStatusManager | None" = None

    def __new__(cls) -> "CollectorStatusManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._collectors: dict[str, CollectorStatus] = {}
        self._alert_cooldown: dict[str, float] = {}
        self._alert_cooldown_seconds: int = 1800

    def register(self, name: str, freshness_seconds: int | None = None) -> None:
        if name not in self._collectors:
            self._collectors[name] = CollectorStatus(name=name, data_freshness_seconds=freshness_seconds)
            logger.info(f"📊 수집기 등록: {name} (신선도: {freshness_seconds}s)")

    def record_success(self, name: str, data: dict[str, Any] | None = None) -> None:
        if name not in self._collectors:
            self.register(name)
        status = self._collectors[name]
        status.last_success = datetime.now()
        status.last_attempt = datetime.now()
        status.consecutive_failures = 0
        status.total_success += 1
        status.is_healthy = True
        status.last_error = None
        if data is not None:
            status.last_data = data
        logger.debug(f"✅ {name} 수집 성공 (총 {status.total_success}회)")

    def record_failure(self, name: str, error: str) -> None:
        if name not in self._collectors:
            self.register(name)
        status = self._collectors[name]
        status.last_attempt = datetime.now()
        status.consecutive_failures += 1
        status.total_failures += 1
        status.last_error = error
        if status.consecutive_failures >= 3:
            status.is_healthy = False
        logger.warning(f"⚠️ {name} 수집 실패 ({status.consecutive_failures}회 연속): {error}")

    def get_status(self, name: str) -> CollectorStatus | None:
        return self._collectors.get(name)

    def get_all_status(self) -> dict[str, CollectorStatus]:
        return self._collectors

    def is_fresh(self, name: str) -> bool:
        status = self._collectors.get(name)
        if not status or not status.last_success:
            return False
        if status.data_freshness_seconds is None:
            return True
        elapsed = (datetime.now() - status.last_success).total_seconds()
        return elapsed < status.data_freshness_seconds

    def should_retry(self, name: str) -> bool:
        status = self._collectors.get(name)
        if not status or not status.last_attempt:
            return True
        backoff = min(2 ** (status.consecutive_failures - 1), 60)
        elapsed = (datetime.now() - status.last_attempt).total_seconds()
        return bool(elapsed >= backoff)

    def should_alert(self, name: str) -> bool:
        now = time.time()
        last_alert = self._alert_cooldown.get(name, 0.0)
        if now - last_alert < self._alert_cooldown_seconds:
            return False
        status = self._collectors.get(name)
        if not status:
            return False
        return bool(status.consecutive_failures >= 3)

    def mark_alert_sent(self, name: str) -> None:
        self._alert_cooldown[name] = time.time()

    def get_summary(self) -> dict[str, Any]:
        total = len(self._collectors)
        healthy = sum(1 for s in self._collectors.values() if s.is_healthy)
        fresh = sum(1 for s in self._collectors.values() if self.is_fresh(s.name))
        return {
            "total": total,
            "healthy": healthy,
            "unhealthy": total - healthy,
            "fresh": fresh,
            "stale": total - fresh,
            "collectors": {
                name: {
                    "is_healthy": s.is_healthy,
                    "is_fresh": self.is_fresh(name),
                    "consecutive_failures": s.consecutive_failures,
                    "last_success": s.last_success.isoformat() if s.last_success else None,
                    "last_error": s.last_error,
                }
                for name, s in self._collectors.items()
            },
        }


collector_status = CollectorStatusManager()
