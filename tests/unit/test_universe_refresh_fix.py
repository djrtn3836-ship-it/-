"""유니버스 자동 갱신 실버그 회귀 테스트 (2026-10-17).

발견: `RuntimeMode.is_safe()`(property를 메서드로 호출) → TypeError →
토 09:00 유니버스 자동 갱신이 예외 흡수로 **조용히 실패만 반복**(P6-1 이후).
수정: `get_runtime_mode().is_safe` 인스턴스 접근.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.runtime_mode import reset_runtime_mode  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]


class TestUniverseRefreshFix:

    def test_safe_mode_skips_without_error(self, monkeypatch):
        """TEST_MODE에서 스킵 반환 — 이 경로가 과거 TypeError로 항상 실패했다."""
        monkeypatch.setenv("TEST_MODE", "1")
        reset_runtime_mode()
        try:
            from scheduler.universe_fetcher import scheduled_universe_refresh

            res = asyncio.run(scheduled_universe_refresh())
            assert res["status"] == "skipped"
            assert res["reason"] == "safe_mode"
        finally:
            reset_runtime_mode()

    def test_no_class_level_property_call(self):
        """소스 가드: 클래스 직접 접근/메서드 호출 패턴 재발 금지."""
        src = (_ROOT / "scheduler" / "universe_fetcher.py").read_text(encoding="utf-8")
        assert "RuntimeMode.is_safe()" not in src
        assert "get_runtime_mode().is_safe" in src
