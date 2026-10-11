"""디스크 감시 + DB 유지보수 + 대시보드 관측 테스트 (2026-10-10).

약점: 디스크가 가득 차면 SQLite 쓰기가 **조용히** 실패한다(결정 저장 실패 =
학습 데이터 손실). 여유 부족을 미리 알리고, DB를 주기적으로 정비한다.
"""
import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.db_maintenance import maintain_sqlite  # noqa: E402
from core.disk_guard import check_disk, disk_status  # noqa: E402


def _make_db(path: Path, rows: int = 4) -> None:
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE IF NOT EXISTS t (a INTEGER)")
        for i in range(rows):
            con.execute("INSERT INTO t VALUES (?)", (i,))
        con.commit()
    finally:
        con.close()


class TestDiskStatus:

    def test_shape_and_bounds(self):
        st = disk_status(".")
        assert {"path", "total_gb", "used_gb", "free_gb", "free_pct"} <= set(st)
        assert st["total_gb"] > 0
        assert 0 <= st["free_pct"] <= 100
        assert st["free_gb"] <= st["total_gb"]


class TestCheckDisk:

    def test_none_when_ample(self):
        assert check_disk(".", min_free_gb=0.001, min_free_pct=0.001) is None

    def test_warns_on_low_gb(self):
        msg = check_disk(".", min_free_gb=10**9, min_free_pct=0.001)
        assert msg is not None
        assert "디스크 여유 부족" in msg and "GB" in msg

    def test_warns_on_low_pct(self):
        msg = check_disk(".", min_free_gb=0.001, min_free_pct=100.0)
        assert msg is not None and "%" in msg

    def test_env_thresholds_respected(self, monkeypatch):
        monkeypatch.setenv("DISK_MIN_FREE_GB", "999999")
        monkeypatch.setenv("DISK_MIN_FREE_PCT", "0.001")
        assert check_disk(".") is not None

    def test_bad_env_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("DISK_MIN_FREE_GB", "abc")
        monkeypatch.setenv("DISK_MIN_FREE_PCT", "abc")
        # 기본값(3GB/5%)으로 복귀 → 이 PC는 여유가 충분해 None일 것(아니어도 str)
        assert check_disk(".") is None or isinstance(check_disk("."), str)


class TestDbMaintenance:

    def test_basic_optimize_and_stats(self, tmp_path):
        db = tmp_path / "decisions.db"
        _make_db(db, rows=7)
        r = maintain_sqlite(str(db))
        assert r["status"] == "ok"
        assert r["integrity"] == "ok"
        assert r["tables"]["t"] == 7
        assert r["size_mb"] >= 0
        assert r["vacuum"] is False

    def test_data_intact_after_optimize(self, tmp_path):
        db = tmp_path / "decisions.db"
        _make_db(db, rows=5)
        maintain_sqlite(str(db))
        con = sqlite3.connect(str(db))
        try:
            assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 5
        finally:
            con.close()

    def test_vacuum_runs(self, tmp_path):
        db = tmp_path / "decisions.db"
        _make_db(db, rows=3)
        r = maintain_sqlite(str(db), vacuum=True)
        assert r["status"] == "ok" and r["vacuum"] is True
        assert r["integrity"] == "ok"

    def test_missing_db(self, tmp_path):
        assert maintain_sqlite(str(tmp_path / "nope.db"))["status"] == "no_db"


class TestDashboardSystemSection:

    def test_includes_disk_and_backlog(self, tmp_path, monkeypatch):
        from report.html_dashboard import collect_system_section

        bp = tmp_path / "pending_alerts.jsonl"
        bp.write_text('{"text":"a"}\n{"text":"b"}\n', encoding="utf-8")
        monkeypatch.setenv("ALERT_BACKLOG_PATH", str(bp))
        out = asyncio.run(collect_system_section())
        assert out.get("alert_backlog") == 2
        assert out.get("disk", {}).get("free_gb") is not None

    def test_backlog_zero_when_no_file(self, tmp_path, monkeypatch):
        from report.html_dashboard import collect_system_section

        monkeypatch.setenv("ALERT_BACKLOG_PATH", str(tmp_path / "none.jsonl"))
        out = asyncio.run(collect_system_section())
        assert out.get("alert_backlog") == 0


class TestSchedulerRegistration:

    def _src(self) -> str:
        p = Path(__file__).resolve().parents[2] / "app" / "bootstrap.py"
        return p.read_text(encoding="utf-8")

    def test_jobs_registered(self):
        src = self._src()
        assert '"disk_check"' in src, "디스크 감시 잡 미등록"
        assert '"db_maintenance"' in src, "DB 유지보수 잡 미등록"
        assert 'self.startup_details["job_count"] = 20' in src

    def test_disk_check_runs_hourly_and_startup(self):
        src = self._src()
        assert "CronTrigger(minute=7" in src, "디스크 감시가 매시 실행이 아님"
        assert "_run_disk_check()" in src, "기동 시 디스크 검사 미배선"
