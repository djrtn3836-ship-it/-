# -*- coding: utf-8 -*-
"""tests/unit/test_db_selection.py - DB 선택 플래그 실동작 검증.

배경: `.env`의 `DB_TYPE`과 `SQLITE_DB_PATH`는 코드에서 읽히지 않는
'죽은 플래그'였다. 실제로 동작하도록 배선했는지 검증한다.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from infrastructure.database.postgres_manager import get_active_db_manager

ROOT = Path(__file__).resolve().parent.parent.parent


class TestDbTypeFlag:
    def test_sqlite_forces_sqlite(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_TYPE", "sqlite")
        assert type(get_active_db_manager()).__name__ == "DatabaseManager"

    def test_postgres_without_url_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_TYPE", "postgres")
        # DATABASE_URL 미설정 환경이므로 SQLite로 폴백(크래시 금지)
        assert type(get_active_db_manager()).__name__ == "DatabaseManager"

    def test_unset_uses_sqlite_when_no_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DB_TYPE", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert type(get_active_db_manager()).__name__ == "DatabaseManager"

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_TYPE", "SQLITE")
        assert type(get_active_db_manager()).__name__ == "DatabaseManager"


class TestSqlitePathFlag:
    def test_env_overrides_default_path(self) -> None:
        env = {**os.environ, "SQLITE_DB_PATH": "data/custom_xyz.db"}
        proc = subprocess.run(
            [sys.executable, "-c", "from data.db_manager import DB_PATH; print(DB_PATH)"],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert "custom_xyz.db" in proc.stdout

    def test_default_path_without_env(self) -> None:
        env = dict(os.environ)
        env.pop("SQLITE_DB_PATH", None)
        proc = subprocess.run(
            [sys.executable, "-c", "from data.db_manager import DB_PATH; print(DB_PATH)"],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        assert "decisions.db" in proc.stdout
