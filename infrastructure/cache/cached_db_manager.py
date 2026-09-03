# -*- coding: utf-8 -*-
"""
infrastructure/cache/cached_db_manager.py - v1.1.0 (Session 27)

CachedDbManager: DatabaseManager를 감싸는 Redis 캐시 래퍼.

v1.1.0 변경: raw_db 프로퍼티 추가 (core/container.py가 원본 DB 타입을
판별할 때 private 속성(_db)에 직접 접근하지 않도록 공개 인터페이스 제공).

설계 원칙:
    - 데코레이터 패턴: DatabaseManager의 모든 기존 메서드를 그대로 위임
    - get_ohlcv()만 Redis 캐시 계층 추가 (가장 빈번하게 호출되는 메서드)
    - Redis 미활성 / 캐시 미스 / 역직렬화 실패 시 항상 DB 직접 조회로 폴백
    - 캐시 키 형식: "ohlcv:{ticker}:{period}"
    - 기본 TTL: 120초 (2분, 장중 실시간 데이터 주기와 균형)
    - invalidate_ohlcv(ticker): 특정 종목 OHLCV 캐시 전체 삭제 (저장 후 호출)
    - data/db_manager.py 원본 파일은 이 세션에서 전혀 수정하지 않음
      (이미 검증된 1060개 테스트 대상 코드에 회귀 위험 원천 차단)

사용 예::
    db = CachedDbManager(DatabaseManager(), redis_cache)
    ohlcv = await db.get_ohlcv("005930", 14)  # Redis 히트 시 DB 미조회
    await db.save_ohlcv("005930", "2026-09-03", {...})  # 저장 후 캐시 무효화
"""

import logging
from typing import Any, Dict, List, Optional

from data.db_manager import DatabaseManager
from infrastructure.cache.redis_cache import RedisCache

logger = logging.getLogger(__name__)

_OHLCV_TTL = 120          # OHLCV 캐시 TTL (초)
_OHLCV_KEY_PREFIX = "ohlcv"


class CachedDbManager:
    """Redis 캐시 계층이 추가된 DatabaseManager 래퍼.

    DatabaseManager의 모든 public 메서드를 그대로 위임하며,
    get_ohlcv()에만 캐시 계층을 추가합니다. Redis가 비활성화되어 있거나
    연결에 실패한 경우 투명하게 원본 DatabaseManager로 폴백합니다.

    Args:
        db: 원본 DatabaseManager 인스턴스
        cache: RedisCache 인스턴스
        ohlcv_ttl: OHLCV 캐시 TTL (초, 기본 120)
    """

    def __init__(
        self,
        db: DatabaseManager,
        cache: RedisCache,
        ohlcv_ttl: int = _OHLCV_TTL,
    ) -> None:
        self._db = db
        self._cache = cache
        self._ohlcv_ttl = ohlcv_ttl
        self._cache_hits = 0
        self._cache_misses = 0

    # ─── 원본 DB 접근 (컨테이너의 타입 판별용) ─────────────────────

    @property
    def raw_db(self) -> DatabaseManager:
        """내부 원본 DB 매니저 반환 (core/container.py의 is_postgres_active 등에서 사용)."""
        return self._db

    # ─── 캐시 계층이 추가된 메서드 ────────────────────────────────

    async def get_ohlcv(self, ticker: str, period: int = 14) -> List[Dict[str, Any]]:
        """OHLCV 조회 (Redis 캐시 우선, 미스 시 DB 조회 후 캐시 저장).

        캐시 키 형식: "ohlcv:{ticker}:{period}"

        Args:
            ticker: 종목 코드
            period: 조회 기간 (영업일 수)

        Returns:
            List[Dict]: OHLCV 레코드 목록 (오래된 순)
        """
        cache_key = f"{_OHLCV_KEY_PREFIX}:{ticker}:{period}"

        cached = await self._cache.get(cache_key)
        if cached is not None:
            self._cache_hits += 1
            logger.debug(f"OHLCV 캐시 히트: {cache_key}")
            return cached

        self._cache_misses += 1
        logger.debug(f"OHLCV 캐시 미스: {cache_key} -> DB 조회")

        result = await self._db.get_ohlcv(ticker, period)
        if result:
            await self._cache.set(cache_key, result, ttl=self._ohlcv_ttl)
        return result

    async def save_ohlcv(self, ticker: str, date: str, ohlcv: dict) -> None:
        """OHLCV 저장 후 해당 종목 캐시 무효화."""
        await self._db.save_ohlcv(ticker, date, ohlcv)
        await self.invalidate_ohlcv(ticker)

    async def save_ohlcv_batch(self, records: List[tuple]) -> int:
        """OHLCV 배치 저장 후 관련 종목 캐시 무효화."""
        count = await self._db.save_ohlcv_batch(records)
        if records:
            tickers = list({r[0] for r in records})
            for ticker in tickers:
                await self.invalidate_ohlcv(ticker)
        return count

    async def invalidate_ohlcv(self, ticker: str) -> int:
        """특정 종목의 모든 OHLCV 캐시 삭제 (period 무관).

        Args:
            ticker: 종목 코드

        Returns:
            삭제된 캐시 키 수
        """
        pattern = f"{_OHLCV_KEY_PREFIX}:{ticker}:*"
        deleted = await self._cache.delete_pattern(pattern)
        if deleted:
            logger.debug(f"OHLCV 캐시 무효화: {ticker} ({deleted}개 키)")
        return deleted

    # ─── 캐시 통계 ──────────────────────────────────────────────────

    def get_cache_stats(self) -> Dict[str, Any]:
        """캐시 히트/미스 통계 반환 (헬스체크용)."""
        total = self._cache_hits + self._cache_misses
        hit_rate = self._cache_hits / total if total > 0 else 0.0
        return {
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "hit_rate": round(hit_rate, 4),
            "redis_active": self._cache.is_active,
        }

    # ─── DatabaseManager 메서드 위임 (get_ohlcv/save_ohlcv 제외) ──

    async def init_db(self) -> None:
        await self._db.init_db()

    async def close(self) -> None:
        await self._db.close()

    async def save_decision(self, analysis: dict) -> None:
        await self._db.save_decision(analysis)

    async def get_decisions_by_date(self, date_str: str) -> List[Dict[str, Any]]:
        return await self._db.get_decisions_by_date(date_str)

    async def get_decisions_by_date_range(
        self, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        return await self._db.get_decisions_by_date_range(start_date, end_date)

    async def save_position(
        self, ticker: str, entry_price: float, current_price: float, qty: int
    ) -> None:
        await self._db.save_position(ticker, entry_price, current_price, qty)

    async def delete_position(self, ticker: str) -> None:
        await self._db.delete_position(ticker)

    async def get_positions(self) -> List[Dict[str, Any]]:
        return await self._db.get_positions()

    async def get_weights(self) -> Dict[str, float]:
        return await self._db.get_weights()

    async def update_weight(self, factor_name: str, new_weight: float) -> None:
        await self._db.update_weight(factor_name, new_weight)

    async def save_outcome(self, outcome: dict) -> None:
        await self._db.save_outcome(outcome)

    async def get_outcome(self, decision_id: int) -> Optional[Dict[str, Any]]:
        return await self._db.get_outcome(decision_id)

    async def get_feedback_stats(self, days: int = 30) -> Dict[str, Any]:
        return await self._db.get_feedback_stats(days)

    async def get_strategy_outcomes(self, days: int = 30) -> List[Dict[str, Any]]:
        return await self._db.get_strategy_outcomes(days)

    async def save_trailing_stops(self, states: Dict[str, dict]) -> int:
        return await self._db.save_trailing_stops(states)

    async def load_trailing_stops(self) -> Dict[str, dict]:
        return await self._db.load_trailing_stops()

    async def clear_trailing_stops(self) -> None:
        await self._db.clear_trailing_stops()

    async def get_ohlcv_range(
        self, ticker: str, start_date: str, end_date: str
    ) -> List[Dict[str, Any]]:
        return await self._db.get_ohlcv_range(ticker, start_date, end_date)

    # ─── db_path 속성 위임 (container.py 등에서 참조할 수 있음) ───

    @property
    def db_path(self) -> Any:
        return self._db.db_path
