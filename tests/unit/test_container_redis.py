# -*- coding: utf-8 -*-
"""tests/unit/test_container_redis.py - Session 27 컨테이너 Redis 통합 테스트 (5개)"""

import asyncio
from unittest.mock import AsyncMock, patch


class TestContainerRedisIntegration:

    def test_db_manager_is_cached_when_redis_active(self):
        from core.container import AppContainer
        from infrastructure.cache.cached_db_manager import CachedDbManager
        container = AppContainer.create_test()
        mock_cache = AsyncMock()
        mock_cache.is_active = True
        container._redis_cache = mock_cache
        container._db_manager = None
        with patch("core.container.get_redis_cache", return_value=mock_cache):
            db = container.db_manager
        assert isinstance(db, CachedDbManager)

    def test_db_manager_is_raw_when_redis_inactive(self):
        from core.container import AppContainer
        from infrastructure.cache.cached_db_manager import CachedDbManager
        container = AppContainer.create_test()
        mock_cache = AsyncMock()
        mock_cache.is_active = False
        container._redis_cache = mock_cache
        container._db_manager = None
        with patch("core.container.get_redis_cache", return_value=mock_cache):
            db = container.db_manager
        assert not isinstance(db, CachedDbManager)

    def test_get_db_type_shows_redis_suffix(self):
        from core.container import AppContainer
        from infrastructure.cache.cached_db_manager import CachedDbManager
        from data.db_manager import DatabaseManager
        from infrastructure.cache.redis_cache import RedisCache
        container = AppContainer.create_test()
        mock_cache = AsyncMock(spec=RedisCache)
        mock_cache.is_active = True
        raw_db = DatabaseManager()
        container._db_manager = CachedDbManager(raw_db, mock_cache)
        assert "Redis" in container.get_db_type()

    def test_initialize_calls_redis_init(self):
        from core.container import AppContainer
        container = AppContainer.create_test()
        mock_cache = AsyncMock()
        mock_cache.init = AsyncMock(return_value=False)
        mock_cache.is_active = False
        mock_db = AsyncMock()
        mock_db.init_db = AsyncMock()
        container._db_manager = mock_db
        container._order_executor = AsyncMock()
        container._portfolio_manager = AsyncMock()
        with patch("core.container.get_redis_cache", return_value=mock_cache), \
             patch("core.container.performance_tracker"):
            asyncio.run(container.initialize())
        mock_cache.init.assert_called_once()

    def test_shutdown_closes_redis(self):
        from core.container import AppContainer
        container = AppContainer.create_test()
        mock_db = AsyncMock()
        mock_kiwoom = AsyncMock()
        mock_cache = AsyncMock()
        mock_cache.close = AsyncMock()
        container._db_manager = mock_db
        container._kiwoom = mock_kiwoom
        container._portfolio_manager = AsyncMock()
        container._redis_cache = mock_cache
        with patch("core.container.performance_tracker") as mock_pt:
            mock_pt.stop = AsyncMock()
            asyncio.run(container.shutdown())
        mock_cache.close.assert_called_once()
