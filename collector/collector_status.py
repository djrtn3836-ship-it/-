"""
collector/collector_status.py - v1.0 FINAL (데이터 수집기 통합 상태 관리)
- 각 수집기의 마지막 성공 시간, 연속 실패 횟수, 데이터 신선도 추적
- 수집 실패 시 중앙화된 경고 및 재시도 트리거
- Telegram 알림과 연동
"""

import time
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from dataclasses import dataclass, field
import asyncio
import logging

logger = logging.getLogger(__name__)


@dataclass
class CollectorStatus:
    """개별 수집기 상태"""
    name: str
    last_success: Optional[datetime] = None
    last_attempt: Optional[datetime] = None
    consecutive_failures: int = 0
    total_success: int = 0
    total_failures: int = 0
    last_error: Optional[str] = None
    is_healthy: bool = True
    data_freshness_seconds: Optional[int] = None  # 데이터 유효 TTL
    last_data: Optional[Dict] = None


class CollectorStatusManager:
    """수집기 상태 관리자 (싱글톤)"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._collectors: Dict[str, CollectorStatus] = {}
        self._alert_cooldown: Dict[str, float] = {}
        self._alert_cooldown_seconds = 1800  # 30분

    def register(self, name: str, freshness_seconds: Optional[int] = None):
        """수집기 등록"""
        if name not in self._collectors:
            self._collectors[name] = CollectorStatus(
                name=name,
                data_freshness_seconds=freshness_seconds
            )
            logger.info(f"📊 수집기 등록: {name} (신선도: {freshness_seconds}s)")

    def record_success(self, name: str, data: Optional[Dict] = None):
        """성공 기록"""
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

    def record_failure(self, name: str, error: str):
        """실패 기록"""
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

    def get_status(self, name: str) -> Optional[CollectorStatus]:
        """특정 수집기 상태 조회"""
        return self._collectors.get(name)

    def get_all_status(self) -> Dict[str, CollectorStatus]:
        """모든 수집기 상태 조회"""
        return self._collectors

    def is_fresh(self, name: str) -> bool:
        """데이터가 신선한지 확인 (TTL 기준)"""
        status = self._collectors.get(name)
        if not status or not status.last_success:
            return False
        if status.data_freshness_seconds is None:
            return True
        elapsed = (datetime.now() - status.last_success).total_seconds()
        return elapsed < status.data_freshness_seconds

    def should_retry(self, name: str) -> bool:
        """재시도가 필요한지 확인 (백오프 포함)"""
        status = self._collectors.get(name)
        if not status or not status.last_attempt:
            return True
        # 연속 실패 횟수에 따른 백오프: 2^failures 초 (최대 60초)
        backoff = min(2 ** (status.consecutive_failures - 1), 60)
        elapsed = (datetime.now() - status.last_attempt).total_seconds()
        return elapsed >= backoff

    def should_alert(self, name: str) -> bool:
        """경고를 보낼지 확인 (쿨다운)"""
        now = time.time()
        last_alert = self._alert_cooldown.get(name, 0)
        if now - last_alert < self._alert_cooldown_seconds:
            return False
        status = self._collectors.get(name)
        if not status:
            return False
        # 3회 연속 실패 시 경고
        return status.consecutive_failures >= 3

    def mark_alert_sent(self, name: str):
        """경고 전송 기록"""
        self._alert_cooldown[name] = time.time()

    def get_summary(self) -> Dict:
        """전체 상태 요약"""
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
            }
        }

# 전역 인스턴스
collector_status = CollectorStatusManager()