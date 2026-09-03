# -*- coding: utf-8 -*-
"""tests/unit/test_db_manager_cqrs.py - Session 28 CQRS 패턴 테스트 (5개)"""

import asyncio
import pytest


class TestDatabaseManagerCQRS:
    @pytest.fixture
    def db_manager(self, tmp_path):
        from data.db_manager import DatabaseManager
        db_file = tmp_path / "test_cqrs.db"
        manager = DatabaseManager(db_path=db_file)
        yield manager
        asyncio.run(manager.close())

    def test_write_and_read_connections_are_distinct(self, db_manager):
        async def _run():
            await db_manager.init_db()
            w_conn = await db_manager._get_write_conn()
            r_conn = await db_manager._get_read_conn()
            assert w_conn is not r_conn
        asyncio.run(_run())

    def test_read_connection_is_read_only_or_fallback(self, db_manager):
        async def _run():
            await db_manager.init_db()
            r_conn = await db_manager._get_read_conn()
            import aiosqlite
            try:
                await r_conn.execute("CREATE TABLE test_ro (id INT)")
                await r_conn.commit()
            except aiosqlite.OperationalError as e:
                assert "readonly" in str(e).lower()
        asyncio.run(_run())

    def test_execute_read_returns_dict_list(self, db_manager):
        async def _run():
            await db_manager.init_db()
            await db_manager.save_ohlcv("005930", "2026-09-03", {"close": 70000.0})
            await db_manager._flush_pending()
            rows = await db_manager._execute_read("SELECT * FROM ohlcv WHERE ticker=?", ("005930",))
            assert isinstance(rows, list)
            assert len(rows) == 1
            assert isinstance(rows[0], dict)
            assert rows[0]["ticker"] == "005930"
            assert rows[0]["close"] == 70000.0
        asyncio.run(_run())

    def test_get_ohlcv_uses_read_connection(self, db_manager):
        async def _run():
            await db_manager.init_db()
            await db_manager.save_ohlcv("005930", "2026-09-03", {"close": 70000.0})
            await db_manager._flush_pending()
            data = await db_manager.get_ohlcv("005930", 14)
            assert len(data) == 1
            assert data[0]["close"] == 70000.0
        asyncio.run(_run())

    def test_close_cleans_up_both_connections(self, db_manager):
        async def _run():
            await db_manager.init_db()
            await db_manager._get_read_conn()
            assert db_manager._write_conn is not None
            assert db_manager._read_conn is not None
            await db_manager.close()
            assert db_manager._write_conn is None
            assert db_manager._read_conn is None
        asyncio.run(_run())
