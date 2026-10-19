"""주말 유지보수 모드 테스트 (2026-10-17 — 사용자 승인).

배경: 재부팅으로 금요일 인스턴스가 죽으면 주말 잡(토 유니버스/일 DB·점검)이
전부 미실행되는 공백 → 비거래일 기동 시 1회성 유지보수.
"""
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.runtime_mode import reset_runtime_mode  # noqa: E402
from scheduler.weekend_maintenance import run_maintenance  # noqa: E402

SAT = datetime(2026, 10, 17, 9, 0)
SUN = datetime(2026, 10, 18, 9, 0)


class _Recorder:
    def __init__(self):
        self.calls = []

    async def refresh(self):
        self.calls.append("refresh")
        return {"status": "ok"}

    async def readiness(self):
        self.calls.append("readiness")
        return {"ml_ready": True}

    def maintenance(self):
        self.calls.append("maintenance")
        return {"status": "ok"}

    def breadth(self):
        self.calls.append("breadth")
        return 123

    def scorecard(self):
        self.calls.append("scorecard")
        return {"path": "x", "rc": {}}


def _run(now, rec, csv_path):
    os.environ.pop("TEST_MODE", None)
    reset_runtime_mode()
    try:
        return asyncio.run(run_maintenance(
            now_fn=lambda: now,
            refresh_fn=rec.refresh,
            readiness_fn=rec.readiness,
            maintenance_fn=rec.maintenance,
            breadth_fn=rec.breadth,
            scorecard_fn=rec.scorecard,
            csv_path=csv_path,
        ))
    finally:
        reset_runtime_mode()


class TestWeekendMaintenance:

    def test_saturday_refresh_only(self, tmp_path):
        rec = _Recorder()
        csv = tmp_path / "u.csv"
        csv.write_text("x", encoding="utf-8")
        res = _run(SAT, rec, csv)
        assert rec.calls == ["refresh", "breadth", "scorecard"], "토요일: 유니버스 + 폭 역사 + 성적표"
        assert res["day"] == 5
        assert res["jobs"]["breadth_history"] == 123

    def test_sunday_maintenance_and_readiness(self, tmp_path):
        rec = _Recorder()
        csv = tmp_path / "u.csv"
        csv.write_text("x", encoding="utf-8")  # 방금 갱신됨 → refresh 생략
        res = _run(SUN, rec, csv)
        assert sorted(rec.calls) == ["breadth", "maintenance", "readiness", "scorecard"]
        assert res["day"] == 6

    def test_sunday_stale_csv_triggers_refresh(self, tmp_path):
        rec = _Recorder()
        csv = tmp_path / "u.csv"
        csv.write_text("x", encoding="utf-8")
        old = datetime(2026, 10, 1, 9, 0).timestamp()
        os.utime(csv, (old, old))  # 17일 경과 = 낡음
        _run(SUN, rec, csv)
        assert "refresh" in rec.calls, "CSV가 6일+ 낡으면 일요일에도 갱신"

    def test_job_failure_does_not_block_others(self, tmp_path):
        async def broken_refresh():
            raise RuntimeError("network down")

        rec = _Recorder()
        rec.refresh = broken_refresh
        csv = tmp_path / "u.csv"
        csv.write_text("x", encoding="utf-8")
        old = datetime(2026, 10, 1, 9, 0).timestamp()
        os.utime(csv, (old, old))
        res = _run(SUN, rec, csv)
        assert res["jobs"]["universe_refresh"].startswith("error:")
        assert sorted(rec.calls) == ["breadth", "maintenance", "readiness", "scorecard"], "실패 격리"

    def test_breadth_failure_isolated(self, tmp_path):
        def broken_breadth():
            raise RuntimeError("ohlcv 없음")

        rec = _Recorder()
        rec.breadth = broken_breadth
        csv = tmp_path / "u.csv"
        csv.write_text("x", encoding="utf-8")
        res = _run(SUN, rec, csv)
        assert res["jobs"]["breadth_history"].startswith("error:")
        assert sorted(rec.calls) == ["maintenance", "readiness", "scorecard"], "폭 실패가 다른 잡을 막지 않음"

    def test_scorecard_failure_isolated(self, tmp_path):
        def broken_scorecard():
            raise RuntimeError("도구 오류")

        rec = _Recorder()
        rec.scorecard = broken_scorecard
        csv = tmp_path / "u.csv"
        csv.write_text("x", encoding="utf-8")
        res = _run(SUN, rec, csv)
        assert res["jobs"]["weekly_scorecard"].startswith("error:")
        assert sorted(rec.calls) == ["breadth", "maintenance", "readiness"], "성적표 실패 격리"

    def test_safe_mode_skips(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_MODE", "1")
        reset_runtime_mode()
        rec = _Recorder()
        try:
            res = asyncio.run(run_maintenance(
                now_fn=lambda: SAT,
                refresh_fn=rec.refresh,
                readiness_fn=rec.readiness,
                maintenance_fn=rec.maintenance,
                breadth_fn=rec.breadth,
                scorecard_fn=rec.scorecard,
                csv_path=tmp_path / "u.csv",
            ))
            assert res == {"skipped": "safe_mode"}
            assert rec.calls == []
        finally:
            reset_runtime_mode()

    def test_main_wired_in_entrypoint(self):
        src = (Path(__file__).resolve().parents[2] / "app" / "main.py").read_text(encoding="utf-8")
        assert "run_maintenance" in src and "weekend_maintenance" in src
        assert 'WEEKEND_MAINTENANCE", "1"' in src, "기본 ON (env로 끌 수 있음)"
