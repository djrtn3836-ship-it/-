"""
Feature Store v5.1.2
Fresh/Stale/Expired 상태 + Lineage 지원
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.constants import FeatureStatus
from core.logger import setup_logger

logger = setup_logger("feature_store")


@dataclass
class FeatureMetadata:
    """피처 메타데이터 (Lineage 포함)"""

    version: str
    timestamp: datetime
    source: str  # 'kiwoom', 'dart', 'news'
    ttl: int  # 초 단위
    confidence: float  # 0~1
    stale_threshold: int = 300  # 5분 후 Stale
    lineage: dict[str, Any] = None  # 데이터 출처 추적


@dataclass
class FeatureItem:
    """피처 아이템 (값 + 메타데이터)"""

    value: Any
    metadata: FeatureMetadata


class FeatureStore:
    """특성 저장소 (Fresh/Stale/Expired + Lineage)"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._store: dict[str, FeatureItem] = {}
        self._lock = asyncio.Lock()

    async def set(self, key: str, value: Any, metadata: FeatureMetadata):
        """특성 저장"""
        async with self._lock:
            self._store[key] = FeatureItem(value=value, metadata=metadata)
            logger.debug(f"Feature stored: {key} (source: {metadata.source})")

    async def get(self, key: str) -> Any | None:
        """특성 조회 (신선도 체크)"""
        async with self._lock:
            if key not in self._store:
                return None

            item = self._store[key]
            age = (datetime.now() - item.metadata.timestamp).total_seconds()

            if age > item.metadata.ttl:
                return None

            return item.value

    async def get_with_status(self, key: str) -> tuple[Any | None, FeatureStatus]:
        """특성 + 상태 조회"""
        async with self._lock:
            if key not in self._store:
                return None, FeatureStatus.EXPIRED

            item = self._store[key]
            age = (datetime.now() - item.metadata.timestamp).total_seconds()

            if age > item.metadata.ttl:
                return item.value, FeatureStatus.EXPIRED
            elif age > item.metadata.stale_threshold:
                return item.value, FeatureStatus.STALE
            else:
                return item.value, FeatureStatus.FRESH

    async def get_with_penalty(self, key: str) -> tuple[Any | None, float]:
        """상태 기반 신뢰도 페널티 적용"""
        value, status = await self.get_with_status(key)

        if status == FeatureStatus.EXPIRED:
            return None, 0.0
        elif status == FeatureStatus.STALE:
            return value, 0.5  # Stale 데이터 신뢰도 50%
        else:
            return value, 1.0  # Fresh 데이터 신뢰도 100%

    async def clear_expired(self):
        """만료된 특성 정리"""
        async with self._lock:
            expired_keys = []
            for key, item in self._store.items():
                age = (datetime.now() - item.metadata.timestamp).total_seconds()
                if age > item.metadata.ttl:
                    expired_keys.append(key)

            for key in expired_keys:
                del self._store[key]

            if expired_keys:
                logger.info(f"Cleared {len(expired_keys)} expired features")

    async def get_stats(self) -> dict:
        """통계 반환"""
        fresh = stale = expired = 0
        for key, item in self._store.items():
            age = (datetime.now() - item.metadata.timestamp).total_seconds()
            if age > item.metadata.ttl:
                expired += 1
            elif age > item.metadata.stale_threshold:
                stale += 1
            else:
                fresh += 1

        total = fresh + stale + expired
        return {
            "total": total,
            "fresh": fresh,
            "stale": stale,
            "expired": expired,
            "fresh_rate": fresh / total if total > 0 else 0,
        }
