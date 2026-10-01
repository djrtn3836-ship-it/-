# -*- coding: utf-8 -*-
"""tests/unit/test_data_readiness_monitor.py - 데이터 축적 게이트 검증 (P7)."""

from typing import Any, Dict, List

import pytest

from scheduler import data_readiness_monitor as drm
from scheduler.data_readiness_monitor import (
    MIN_DECISIONS,
    MIN_OUTCOMES,
    check_data_readiness,
)


class _FakeDB:
    def __init__(self, counts: Dict[str, int]) -> None:
        self.counts = counts
        self.closed = False

    async def _execute_read(self, query: str, params: Any = ()) -> List[Dict[str, Any]]:
        for table, n in self.counts.items():
            if table in query:
                return [{"n": n}]
        return [{"n": 0}]

    async def close(self) -> None:
        self.closed = True


class TestCheckDataReadiness:
    async def test_not_ready_when_empty(self) -> None:
        result = await check_data_readiness(_FakeDB({}))

        assert result["ml_ready"] is False
        assert result["progress"] == 0.0
        assert "축적 중" in result["message"]

    async def test_ready_when_thresholds_met(self) -> None:
        db = _FakeDB({"decisions": MIN_DECISIONS, "decision_outcomes": MIN_OUTCOMES})
        result = await check_data_readiness(db)

        assert result["ml_ready"] is True
        assert result["progress"] == 1.0
        assert "검증 가능" in result["message"]

    async def test_partial_progress(self) -> None:
        db = _FakeDB({"decisions": MIN_DECISIONS // 2, "decision_outcomes": MIN_OUTCOMES})
        result = await check_data_readiness(db)

        assert result["ml_ready"] is False
        assert 0.0 < result["progress"] < 1.0

    async def test_outcomes_alone_not_enough(self) -> None:
        db = _FakeDB({"decisions": MIN_DECISIONS, "decision_outcomes": MIN_OUTCOMES - 1})
        assert (await check_data_readiness(db))["ml_ready"] is False

    async def test_includes_ohlcv_and_paper_counts(self) -> None:
        db = _FakeDB({"ohlcv": 230441, "momentum_paper": 7})
        result = await check_data_readiness(db)

        assert result["ohlcv"] == 230441
        assert result["evaluated_paper"] == 7

    async def test_survives_broken_db(self) -> None:
        class _Broken:
            async def _execute_read(self, query: str, params: Any = ()) -> List[Dict[str, Any]]:
                raise RuntimeError("DB 오류")

        result = await check_data_readiness(_Broken())
        assert result["decisions"] == 0 and result["ml_ready"] is False


class TestScheduledCheck:
    async def test_notifies_only_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        notifications: List[Dict[str, Any]] = []
        state: Dict[str, Any] = {}

        async def _fake_notify(result: Dict[str, Any]) -> None:
            notifications.append(result)

        monkeypatch.setattr(drm, "_notify_ready", _fake_notify)
        monkeypatch.setattr(drm, "_load_state", lambda: dict(state))
        monkeypatch.setattr(drm, "_save_state", lambda s: state.update(s))
        monkeypatch.setattr(
            "data.db_manager.DatabaseManager",
            lambda: _FakeDB({"decisions": MIN_DECISIONS, "decision_outcomes": MIN_OUTCOMES}),
        )

        first = await drm.scheduled_data_readiness_check()
        second = await drm.scheduled_data_readiness_check()

        assert first["ml_ready"] and second["ml_ready"]
        assert len(notifications) == 1          # 최초 1회만 알림
        assert state["ml_ready_notified"] is True

    async def test_no_notification_before_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        notifications: List[Dict[str, Any]] = []

        async def _fake_notify(result: Dict[str, Any]) -> None:
            notifications.append(result)

        monkeypatch.setattr(drm, "_notify_ready", _fake_notify)
        monkeypatch.setattr(drm, "_load_state", lambda: {})
        monkeypatch.setattr(drm, "_save_state", lambda s: None)
        monkeypatch.setattr("data.db_manager.DatabaseManager", lambda: _FakeDB({}))

        result = await drm.scheduled_data_readiness_check()

        assert result["ml_ready"] is False
        assert notifications == []
