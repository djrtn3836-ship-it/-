# -*- coding: utf-8 -*-
"""
infrastructure/cache/redis_cache.py - v1.0.1 (Session 27, 버그 수정)

Redis 비동기 캐시 레이어.

설계 원칙:
    - Redis 연결 실패 / 키 없음 / 역직렬화 실패 시 항상 None 반환 (폴백 안전)
    - redis 패키지 미설치 또는 URL 미설정 시 완전 비활성화 (운영 코드 무영향)
    - JSON 직렬화 (pickle 대신 - 보안, 이식성, 가독성)
    - TTL은 호출부에서 결정 (캐시 레이어는 저장/조회만 담당)
    - 연결 풀 싱글톤 (프로세스 당 1개, get_redis_cache()로 접근)

v1.0.1 수정 사항:
    - init()이 모듈 전역 환경변수 플래그가 아니라 인스턴스별 self._url을
      직접 검사하도록 수정. (수정 전 버그: 생성자에 커스텀 url을 넘겨도
      모듈이 처음 로드될 때 REDIS_URL 환경변수가 없었다면 init()이 실제
      연결 시도조차 하지 않고 조용히 False를 반환하던 문제. 이 상태로는
      테스트가 "연결 실패"가 아니라 "애초에 시도하지 않음"이라는 잘못된
      이유로 통과하고 있었음)
    - 생성자에서 url="" (명시적 빈 문자열)과 url=None(미지정, env 기본값
      사용)을 구분하도록 Optional 센티널 패턴 적용.

사용 예::
    cache = get_redis_cache()
    await cache.init()
    await cache.set("ohlcv:005930:14", data, ttl=120)
    result = await cache.get("ohlcv:005930:14")   # None이면 캐시 미스
    await cache.delete("ohlcv:005930:14")
    await cache.close()
"""

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_REDIS_URL: str = os.getenv("REDIS_URL", "")

try:
    import redis.asyncio as aioredis  # redis>=5.0.0
    _REDIS_AVAILABLE = True
except ImportError:
    aioredis = None  # type: ignore
    _REDIS_AVAILABLE = False


class RedisCache:
    """Redis 비동기 캐시 클라이언트 (JSON 직렬화, 폴백 안전).

    redis 패키지 미설치 또는 유효한 URL이 없는 경우 모든 메서드가
    조용히 no-op / None 반환하여 상위 코드가 폴백 로직을 타도록 합니다.
    """

    def __init__(self, url: Optional[str] = None) -> None:
        # url=None(미지정)이면 환경변수 기본값 사용, url=""(명시적 빈값)이면 강제 비활성
        self._url: str = url if url is not None else _REDIS_URL
        self._client: Optional[Any] = None
        self._initialized: bool = False

        if not _REDIS_AVAILABLE:
            logger.info("RedisCache: redis 패키지 미설치 -> 비활성 모드 (폴백 동작)")
        elif not self._url:
            logger.info("RedisCache: URL 미설정 -> 비활성 모드 (폴백 동작)")

    async def init(self) -> bool:
        """Redis 연결 초기화. 성공 시 True, 실패 시 False 반환.

        인스턴스의 self._url을 기준으로 판단하며, 모듈 전역 환경변수
        상태와 무관하게 동작합니다 (테스트 용이성 및 정확성 확보).
        """
        if not _REDIS_AVAILABLE or not self._url:
            return False
        if self._initialized:
            return True
        try:
            self._client = aioredis.from_url(
                self._url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=3.0,
                socket_timeout=1.0,
                retry_on_timeout=False,
            )
            await self._client.ping()
            self._initialized = True
            logger.info(f"RedisCache 연결 성공: {self._url.split('@')[-1]}")
            return True
        except Exception as e:
            logger.warning(f"RedisCache 연결 실패 (SQLite 폴백): {e}")
            self._client = None
            self._initialized = False
            return False

    async def get(self, key: str) -> Optional[Any]:
        """캐시에서 값 조회. 미스 또는 오류 시 None 반환."""
        if not self._initialized or self._client is None:
            return None
        try:
            raw: Optional[str] = await self._client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.debug(f"RedisCache.get 실패 ({key}): {e}")
            return None

    async def set(self, key: str, value: Any, ttl: int = 120) -> bool:
        """값을 TTL과 함께 캐시에 저장. 성공 시 True, 실패 시 False 반환."""
        if not self._initialized or self._client is None:
            return False
        try:
            serialized: str = json.dumps(value, ensure_ascii=False, default=str)
            await self._client.setex(key, ttl, serialized)
            return True
        except Exception as e:
            logger.debug(f"RedisCache.set 실패 ({key}): {e}")
            return False

    async def delete(self, key: str) -> bool:
        """캐시 항목 삭제. 성공 시 True."""
        if not self._initialized or self._client is None:
            return False
        try:
            await self._client.delete(key)
            return True
        except Exception as e:
            logger.debug(f"RedisCache.delete 실패 ({key}): {e}")
            return False

    async def delete_pattern(self, pattern: str) -> int:
        """패턴에 매칭되는 키 전체 삭제. 삭제된 키 수 반환."""
        if not self._initialized or self._client is None:
            return 0
        try:
            keys = await self._client.keys(pattern)
            if not keys:
                return 0
            return await self._client.delete(*keys)
        except Exception as e:
            logger.debug(f"RedisCache.delete_pattern 실패 ({pattern}): {e}")
            return 0

    async def close(self) -> None:
        """연결 종료."""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
            self._initialized = False
            logger.info("RedisCache 연결 종료")

    @property
    def is_active(self) -> bool:
        """현재 Redis 연결이 활성화되어 있는지 반환."""
        return self._initialized

    async def get_stats(self) -> dict:
        """Redis 상태 정보 반환 (헬스체크용)."""
        if not self._initialized or self._client is None:
            return {"active": False, "url": "N/A"}
        try:
            info = await self._client.info("stats")
            return {
                "active": True,
                "url": self._url.split("@")[-1] if "@" in self._url else self._url,
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
            }
        except Exception:
            return {"active": self._initialized, "url": "N/A"}


# ─── 전역 싱글톤 ──────────────────────────────────────────────────────

_redis_cache: Optional[RedisCache] = None


def get_redis_cache() -> RedisCache:
    """RedisCache 전역 싱글톤 반환."""
    global _redis_cache
    if _redis_cache is None:
        _redis_cache = RedisCache()
    return _redis_cache
