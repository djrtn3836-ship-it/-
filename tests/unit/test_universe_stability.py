# -*- coding: utf-8 -*-
"""tests/unit/test_universe_stability.py - 유니버스 파이프라인 안정화 검증 (P6 고도화)."""

import time
from pathlib import Path
from typing import Any, Dict

import pytest

from infrastructure.market_data import universe_provider as up
from scheduler import universe_fetcher as uf


@pytest.fixture(autouse=True)
def _reset_provider_state(monkeypatch: pytest.MonkeyPatch):
    """전역 상태를 매 테스트마다 초기화한다."""
    monkeypatch.setattr(up, "_LAST_SOURCE", "unknown")
    monkeypatch.setattr(up, "_LAST_MARKET_MAP", {})
    monkeypatch.setattr(up, "_LAST_CSV_AGE_DAYS", None)
    yield


def _write_csv(path: Path, rows: int = 120) -> Path:
    lines = ["code,name,market"]
    for i in range(rows):
        market = "KOSPI" if i % 2 == 0 else "KOSDAQ"
        lines.append(f"{100000 + i:06d},종목{i},{market}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestFreshness:
    def test_stale_csv_sets_source(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        csv = _write_csv(tmp_path / "u.csv")
        old = time.time() - 30 * 86400          # 30일 전
        import os

        os.utime(csv, (old, old))
        monkeypatch.setattr(up, "CSV_PATH", csv)

        up.get_universe()

        assert up.get_last_source() == "csv_stale"
        assert up.is_stale() is True
        assert up.is_fallback() is False
        assert up.get_csv_age_days() > 29

    def test_fresh_csv_sets_source(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        csv = _write_csv(tmp_path / "u.csv")
        monkeypatch.setattr(up, "CSV_PATH", csv)

        up.get_universe()

        assert up.get_last_source() == "csv"
        assert up.is_stale() is False

    def test_age_limit_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UNIVERSE_MAX_AGE_DAYS", "3")
        assert up.max_age_days() == 3.0

        monkeypatch.setenv("UNIVERSE_MAX_AGE_DAYS", "abc")
        assert up.max_age_days() == float(up.DEFAULT_MAX_AGE_DAYS)

    def test_missing_csv_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(up, "CSV_PATH", tmp_path / "없음.csv")
        up.get_universe()

        assert up.get_last_source() == "fallback"
        assert up.is_fallback() is True


class TestMarketPreservation:
    def test_market_map_captured(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        csv = _write_csv(tmp_path / "u.csv", rows=120)
        monkeypatch.setattr(up, "CSV_PATH", csv)

        up.get_universe()
        m = up.get_last_market_map()

        assert len(m) == 120
        assert set(m.values()) == {"KOSPI", "KOSDAQ"}

    def test_stockuniverse_keeps_market(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        csv = _write_csv(tmp_path / "u.csv", rows=120)
        monkeypatch.setattr(up, "CSV_PATH", csv)

        su = up.StockUniverse()
        su._instance = None                       # 싱글턴 초기화
        su = up.StockUniverse()
        markets = {s.market for s in su.get_all()}

        assert markets == {"KOSPI", "KOSDAQ"}
        su._instance = None


class TestScheduledRefresh:
    def test_skips_in_safe_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import core.runtime_mode as rm

        monkeypatch.setattr(rm.RuntimeMode, "is_safe", staticmethod(lambda: True))
        calls: list = []
        monkeypatch.setattr(uf, "UniverseFetcher", lambda *a, **k: calls.append(1))

        result = _run(uf.scheduled_universe_refresh())

        assert result["status"] == "skipped"
        assert calls == []

    def test_rejects_shrunken_fetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import core.runtime_mode as rm

        monkeypatch.setattr(rm.RuntimeMode, "is_safe", staticmethod(lambda: False))
        monkeypatch.setattr(rm.RuntimeMode, "mock_data", False)
        monkeypatch.setattr(uf, "_count_existing", lambda p: 500)

        class _Fetcher:
            def fetch(self, pages: int = 4):
                from scheduler.universe_fetcher import FetchResult, UniverseEntry

                r = FetchResult()
                r.entries = [UniverseEntry("005930", "삼성전자", "KOSPI")]
                return r

        monkeypatch.setattr(uf, "UniverseFetcher", lambda *a, **k: _Fetcher())
        saved: list = []
        monkeypatch.setattr(uf, "save_universe_csv", lambda *a, **k: saved.append(1))

        result = _run(uf.scheduled_universe_refresh())

        assert result["status"] == "rejected"
        assert saved == []

    def test_saves_when_healthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import core.runtime_mode as rm
        from scheduler.universe_fetcher import FetchResult, UniverseEntry

        monkeypatch.setattr(rm.RuntimeMode, "is_safe", staticmethod(lambda: False))
        monkeypatch.setattr(rm.RuntimeMode, "mock_data", False)
        monkeypatch.setattr(uf, "_count_existing", lambda p: 100)

        class _Fetcher:
            def fetch(self, pages: int = 4):
                r = FetchResult()
                r.entries = [UniverseEntry(f"{100000 + i:06d}", f"종목{i}", "KOSPI") for i in range(120)]
                return r

        monkeypatch.setattr(uf, "UniverseFetcher", lambda *a, **k: _Fetcher())
        monkeypatch.setattr(uf, "save_universe_csv", lambda entries, path: len(entries))

        result = _run(uf.scheduled_universe_refresh())

        assert result["status"] == "ok"
        assert result["new"] == 120

    def test_empty_fetch_keeps_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import core.runtime_mode as rm

        monkeypatch.setattr(rm.RuntimeMode, "is_safe", staticmethod(lambda: False))
        monkeypatch.setattr(rm.RuntimeMode, "mock_data", False)

        class _Fetcher:
            def fetch(self, pages: int = 4):
                from scheduler.universe_fetcher import FetchResult

                return FetchResult()

        monkeypatch.setattr(uf, "UniverseFetcher", lambda *a, **k: _Fetcher())
        result = _run(uf.scheduled_universe_refresh())

        assert result["status"] == "empty"


def _run(coro: Any) -> Dict[str, Any]:
    import asyncio

    return asyncio.run(coro)


class TestNoSilentNoOp:
    """회귀 방지: async 메서드를 await 없이 호출하면 무음 미실행이 된다."""

    def test_bootstrap_has_no_unawaited_coroutines(self) -> None:
        import ast

        path = Path(__file__).parent.parent.parent / "app" / "bootstrap.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))

        hits = []
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            async_methods = {m.name for m in cls.body if isinstance(m, ast.AsyncFunctionDef)}
            for node in ast.walk(cls):
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    f = node.value.func
                    if (
                        isinstance(f, ast.Attribute)
                        and isinstance(f.value, ast.Name)
                        and f.value.id == "self"
                        and f.attr in async_methods
                    ):
                        hits.append(f"{path.name}:{node.lineno} self.{f.attr}()")

        assert hits == [], f"await 누락(무음 미실행): {hits}"


class TestSchedulerRegistration:
    """스케줄러 잡 등록 회귀 방지 — 잡이 조용히 사라지는 것을 막는다."""

    def _jobs(self) -> list:
        import ast
        import re

        src = (Path(__file__).parent.parent.parent / "app" / "bootstrap.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        names = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_job_with_retry"
            ):
                found = [
                    a.value
                    for a in node.args
                    if isinstance(a, ast.Constant)
                    and isinstance(a.value, str)
                    and re.fullmatch(r"[a-z_]{3,}", a.value)
                ]
                names.append(found[0] if found else "?")
        return names

    def test_expected_jobs_registered(self) -> None:
        jobs = self._jobs()

        for expected in (
            "daily_report", "weekly_pdf", "momentum_report", "macro_update",
            "market_risk_check", "data_readiness", "universe_refresh",
            "correlation_check", "dashboard", "hyperparameter_tuning",
            "alert_verifier", "phase_transition_check", "feedback_learning",
        ):
            assert expected in jobs, f"{expected} 잡이 등록되지 않음"

    def test_job_count_matches_declared(self) -> None:
        import re

        src = (Path(__file__).parent.parent.parent / "app" / "bootstrap.py").read_text(encoding="utf-8")
        declared = int(re.search(r'self\.startup_details\["job_count"\] = (\d+)', src).group(1))

        assert len(self._jobs()) == declared, (
            f"등록된 잡 {len(self._jobs())}개 != 선언된 job_count {declared}개"
        )
