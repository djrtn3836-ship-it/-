# -*- coding: utf-8 -*-
"""tests/unit/test_container_db_switching.py - AppContainer DB 스위칭 테스트"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

import core.container as mod


class TestContainerDbSwitching:

    def test_default_is_sqlite(self, monkeypatch):
        """DATABASE_URL 미설정 시 SQLite 사용."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        container = mod.AppContainer()
        assert container.get_db_type() == "SQLite"
        assert container.is_postgres_active() is False

    def test_is_postgres_active_false_before_access(self):
        """db_manager 프로퍼티 접근 전에는 is_postgres_active()가 False."""
        container = mod.AppContainer()
        assert container.is_postgres_active() is False

    def test_db_manager_is_singleton_within_container(self, monkeypatch):
        """동일 컨테이너에서 db_manager는 항상 같은 객체."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        container = mod.AppContainer()
        assert container.db_manager is container.db_manager

    def test_create_test_uses_tmp_path(self, tmp_path):
        """create_test(tmp_path)는 SQLite DatabaseManager를 tmp_path에 생성."""
        from data.db_manager import DatabaseManager
        container = mod.AppContainer.create_test(tmp_path)
        assert isinstance(container._db_manager, DatabaseManager)
        assert "test.db" in str(container._db_manager.db_path)

    def test_portfolio_manager_is_global_singleton(self):
        """서로 다른 컨테이너 인스턴스도 동일한 PortfolioManager를 공유."""
        c1 = mod.AppContainer()
        c2 = mod.AppContainer()
        assert c1.portfolio_manager is c2.portfolio_manager

    def test_initialize_preserves_performance_tracker_call(self):
        """🔥 회귀 방지 테스트: initialize()는 반드시 performance_tracker.initialize()를
        호출해야 합니다. bootstrap.py의 start_performance_tracker()가 이 호출에
        의존하므로, 제거되면 PerformanceTracker가 영원히 시작되지 않는 회귀가 발생합니다.
        """
        container = mod.AppContainer()
        mock_db = AsyncMock()
        container._db_manager = mock_db
        container._order_executor = AsyncMock()
        container._portfolio_manager = AsyncMock()

        with patch("core.container.performance_tracker") as mock_pt:
            asyncio.run(container.initialize())
            mock_pt.initialize.assert_called_once_with(mock_db)

    def test_initialize_calls_db_init(self):
        """initialize()가 db_manager.init_db()를 호출."""
        container = mod.AppContainer()
        mock_db = AsyncMock()
        container._db_manager = mock_db
        container._order_executor = AsyncMock()
        container._portfolio_manager = AsyncMock()

        with patch("core.container.performance_tracker"):
            asyncio.run(container.initialize())
        mock_db.init_db.assert_awaited_once()

    def test_postgres_init_failure_falls_back_to_sqlite(self):
        """PostgreSQL 연결 실패 시 SQLite로 자동 폴백."""
        class PostgresManager:  # 로컬 가짜 클래스: 타입명이 "PostgresManager"여야
            def __init__(self):
                self.init_db = AsyncMock(side_effect=ConnectionError("연결 거부"))

        container = mod.AppContainer()
        container._db_manager = PostgresManager()
        container._order_executor = AsyncMock()
        container._portfolio_manager = AsyncMock()

        with (
            patch("core.container.DatabaseManager") as MockSQLite,
            patch("core.container.performance_tracker") as mock_pt,
        ):
            mock_sqlite = AsyncMock()
            MockSQLite.return_value = mock_sqlite
            asyncio.run(container.initialize())

        assert container._db_manager is mock_sqlite
        mock_sqlite.init_db.assert_awaited_once()
        mock_pt.initialize.assert_called_once_with(mock_sqlite)

    def test_sqlite_failure_does_not_trigger_fallback_loop(self):
        """이미 SQLite 상태에서 init_db() 실패 시, 폴백을 시도하지 않고 예외를 그대로 전파."""
        container = mod.AppContainer()
        mock_db = AsyncMock()
        mock_db.init_db = AsyncMock(side_effect=RuntimeError("disk full"))
        container._db_manager = mock_db
        container._order_executor = AsyncMock()
        container._portfolio_manager = AsyncMock()

        with pytest.raises(RuntimeError):
            asyncio.run(container.initialize())

    def test_shutdown_calls_close(self):
        """shutdown()이 db_manager.close()를 호출."""
        container = mod.AppContainer()
        mock_db = AsyncMock()
        container._db_manager = mock_db
        container._kiwoom = AsyncMock()
        container._portfolio_manager = AsyncMock()

        with patch("core.container.performance_tracker"):
            asyncio.run(container.shutdown())
        mock_db.close.assert_awaited_once()
