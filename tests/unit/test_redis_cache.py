# -*- coding: utf-8 -*-
"""tests/unit/test_redis_cache.py - Session 27 Redis 캐시 레이어 테스트 (25개, 완결)

Session 27 완결 수정:
    - test_get_ohlcv_cache_miss_stores_in_cache: cached_db_manager.py가
      cache.set()을 호출할 때 ttl을 키워드 인자로 전달하므로
      (await self._cache.set(cache_key, result, ttl=self._ohlcv_ttl)),
      위치 인자 튜플에는 (key, value) 2개만 들어가고 ttl은 kwargs에 들어갑니다.
      기존 테스트는 call_args[2](위치 인자 3번째)를 기대하여 IndexError가
      발생했으며, args, kwargs = call_args로 언패킹하여 kwargs["ttl"]로 검증하도록
      수정했습니다. (다른 모든 테스트는 원본과 100% 동일, 회귀 없음)
"""

import asyncio
import pytest
from unittest.mock import AsyncMock


class TestRedisCacheInit:
    def test_redis_disabled_when_no_url(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        from infrastructure.cache.redis_cache import RedisCache
        cache = RedisCache(url="")
        assert cache.is_active is False

    def test_redis_disabled_returns_none_on_get(self):
        from infrastructure.cache.redis_cache import RedisCache
        cache = RedisCache(url="")
        result = asyncio.run(cache.get("any_key"))
        assert result is None

    def test_redis_disabled_returns_false_on_set(self):
        from infrastructure.cache.redis_cache import RedisCache
        cache = RedisCache(url="")
        result = asyncio.run(cache.set("key", "value", ttl=60))
        assert result is False

    def test_redis_disabled_returns_false_on_delete(self):
        from infrastructure.cache.redis_cache import RedisCache
        cache = RedisCache(url="")
        result = asyncio.run(cache.delete("key"))
        assert result is False

    def test_redis_disabled_stats_shows_inactive(self):
        from infrastructure.cache.redis_cache import RedisCache
        cache = RedisCache(url="")
        stats = asyncio.run(cache.get_stats())
        assert stats["active"] is False

    def test_init_failure_returns_false(self):
        from infrastructure.cache.redis_cache import RedisCache, _REDIS_AVAILABLE
        if not _REDIS_AVAILABLE:
            pytest.skip("redis 패키지 미설치")
        cache = RedisCache(url="redis://invalid-host-that-does-not-exist:6379")
        result = asyncio.run(cache.init())
        assert result is False
        assert cache.is_active is False


class TestRedisCacheMocked:
    def _make_cache_with_mock(self):
        from infrastructure.cache.redis_cache import RedisCache
        cache = RedisCache(url="redis://localhost:6379")
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(return_value=True)
        mock_client.get = AsyncMock(return_value=None)
        mock_client.setex = AsyncMock(return_value=True)
        mock_client.delete = AsyncMock(return_value=1)
        mock_client.keys = AsyncMock(return_value=[])
        mock_client.aclose = AsyncMock()
        cache._client = mock_client
        cache._initialized = True
        return cache, mock_client

    def test_get_returns_none_on_cache_miss(self):
        cache, mock_client = self._make_cache_with_mock()
        mock_client.get = AsyncMock(return_value=None)
        result = asyncio.run(cache.get("ohlcv:005930:14"))
        assert result is None

    def test_get_returns_parsed_json_on_hit(self):
        import json
        cache, mock_client = self._make_cache_with_mock()
        data = [{"date": "2026-09-03", "close": 70000.0}]
        mock_client.get = AsyncMock(return_value=json.dumps(data))
        result = asyncio.run(cache.get("ohlcv:005930:14"))
        assert result == data

    def test_set_calls_setex_with_ttl(self):
        cache, mock_client = self._make_cache_with_mock()
        asyncio.run(cache.set("key", {"value": 1}, ttl=120))
        mock_client.setex.assert_called_once()
        args, kwargs = mock_client.setex.call_args
        assert args[0] == "key"
        assert args[1] == 120

    def test_set_returns_true_on_success(self):
        cache, mock_client = self._make_cache_with_mock()
        result = asyncio.run(cache.set("key", "value", ttl=60))
        assert result is True

    def test_delete_calls_client_delete(self):
        cache, mock_client = self._make_cache_with_mock()
        asyncio.run(cache.delete("ohlcv:005930:14"))
        mock_client.delete.assert_called_once_with("ohlcv:005930:14")

    def test_delete_pattern_calls_keys_then_delete(self):
        cache, mock_client = self._make_cache_with_mock()
        mock_client.keys = AsyncMock(return_value=["ohlcv:005930:14", "ohlcv:005930:30"])
        mock_client.delete = AsyncMock(return_value=2)
        result = asyncio.run(cache.delete_pattern("ohlcv:005930:*"))
        assert result == 2
        mock_client.keys.assert_called_once_with("ohlcv:005930:*")

    def test_delete_pattern_empty_keys_returns_zero(self):
        cache, mock_client = self._make_cache_with_mock()
        mock_client.keys = AsyncMock(return_value=[])
        result = asyncio.run(cache.delete_pattern("ohlcv:999999:*"))
        assert result == 0

    def test_get_handles_json_decode_error_gracefully(self):
        cache, mock_client = self._make_cache_with_mock()
        mock_client.get = AsyncMock(return_value="not-valid-json{{{")
        result = asyncio.run(cache.get("bad_key"))
        assert result is None

    def test_close_calls_aclose(self):
        cache, mock_client = self._make_cache_with_mock()
        asyncio.run(cache.close())
        mock_client.aclose.assert_called_once()
        assert cache.is_active is False


class TestCachedDbManager:
    def _make_cached_db(self):
        from infrastructure.cache.cached_db_manager import CachedDbManager
        from infrastructure.cache.redis_cache import RedisCache

        mock_db = AsyncMock()
        mock_db.get_ohlcv = AsyncMock(return_value=[{"date": "2026-09-03", "close": 70000.0}])
        mock_db.save_ohlcv = AsyncMock()
        mock_db.save_ohlcv_batch = AsyncMock(return_value=2)
        mock_db.db_path = "/tmp/test.db"

        mock_cache = AsyncMock(spec=RedisCache)
        mock_cache.is_active = True
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock(return_value=True)
        mock_cache.delete_pattern = AsyncMock(return_value=1)

        cached_db = CachedDbManager(mock_db, mock_cache, ohlcv_ttl=120)
        return cached_db, mock_db, mock_cache

    def test_get_ohlcv_cache_miss_calls_db(self):
        cached_db, mock_db, mock_cache = self._make_cached_db()
        result = asyncio.run(cached_db.get_ohlcv("005930", 14))
        mock_db.get_ohlcv.assert_called_once_with("005930", 14)
        assert result == [{"date": "2026-09-03", "close": 70000.0}]

    def test_get_ohlcv_cache_miss_stores_in_cache(self):
        """🔧 Session 27 완결 수정: ttl은 키워드 인자로 전달되므로
        args, kwargs = call_args로 언패킹하여 kwargs["ttl"]로 검증."""
        cached_db, mock_db, mock_cache = self._make_cached_db()
        asyncio.run(cached_db.get_ohlcv("005930", 14))
        mock_cache.set.assert_called_once()
        args, kwargs = mock_cache.set.call_args
        assert args[0] == "ohlcv:005930:14"
        assert kwargs["ttl"] == 120

    def test_get_ohlcv_cache_hit_skips_db(self):
        cached_db, mock_db, mock_cache = self._make_cached_db()
        mock_cache.get = AsyncMock(return_value=[{"date": "2026-09-03", "close": 70000.0}])
        asyncio.run(cached_db.get_ohlcv("005930", 14))
        mock_db.get_ohlcv.assert_not_called()

    def test_get_ohlcv_cache_hit_increments_hit_counter(self):
        cached_db, mock_db, mock_cache = self._make_cached_db()
        mock_cache.get = AsyncMock(return_value=[{"close": 70000.0}])
        asyncio.run(cached_db.get_ohlcv("005930", 14))
        stats = cached_db.get_cache_stats()
        assert stats["cache_hits"] == 1
        assert stats["cache_misses"] == 0

    def test_get_ohlcv_cache_miss_increments_miss_counter(self):
        cached_db, mock_db, mock_cache = self._make_cached_db()
        asyncio.run(cached_db.get_ohlcv("005930", 14))
        stats = cached_db.get_cache_stats()
        assert stats["cache_hits"] == 0
        assert stats["cache_misses"] == 1

    def test_save_ohlcv_invalidates_cache(self):
        cached_db, mock_db, mock_cache = self._make_cached_db()
        asyncio.run(cached_db.save_ohlcv("005930", "2026-09-03", {"close": 70000.0}))
        mock_cache.delete_pattern.assert_called_once_with("ohlcv:005930:*")

    def test_save_ohlcv_batch_invalidates_all_tickers(self):
        cached_db, mock_db, mock_cache = self._make_cached_db()
        records = [("005930", "2026-09-03", 0, 0, 0, 70000.0, 0),
                   ("000660", "2026-09-03", 0, 0, 0, 200000.0, 0)]
        asyncio.run(cached_db.save_ohlcv_batch(records))
        assert mock_cache.delete_pattern.call_count == 2

    def test_hit_rate_calculation(self):
        cached_db, mock_db, mock_cache = self._make_cached_db()
        mock_cache.get = AsyncMock(side_effect=[None, [{"close": 70000.0}]])
        asyncio.run(cached_db.get_ohlcv("005930", 14))
        asyncio.run(cached_db.get_ohlcv("005930", 14))
        stats = cached_db.get_cache_stats()
        assert stats["hit_rate"] == pytest.approx(0.5)

    def test_db_path_property_delegated(self):
        cached_db, mock_db, mock_cache = self._make_cached_db()
        assert cached_db.db_path == "/tmp/test.db"

    def test_raw_db_property_returns_original(self):
        cached_db, mock_db, mock_cache = self._make_cached_db()
        assert cached_db.raw_db is mock_db
