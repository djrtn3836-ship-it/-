"""DB 자동 백업 + 정산 순서 회귀 테스트 (2026-10-10).

배경(고친 약점):
① decisions.db 자동 백업 부재 → 온라인 백업 API + 무결성 검사 + 보관 회전
② 정산 잡(16:00/16:10)이 일봉 수집(16:30)보다 먼저 실행돼 **항상 하루 늦게**
   채점되던 순서 문제 → 정산을 일봉 수집 이후(17:05/17:15)로 이동
"""
import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.db_backup import (  # noqa: E402
    backup_sqlite,
    last_backup_age_hours,
    list_backups,
)


def _make_db(path: Path, rows: int = 3) -> None:
    con = sqlite3.connect(str(path))
    try:
        con.execute("CREATE TABLE IF NOT EXISTS t (a INTEGER, b TEXT)")
        for i in range(rows):
            con.execute("INSERT INTO t VALUES (?, ?)", (i, f"row{i}"))
        con.commit()
    finally:
        con.close()


class TestBackupSqlite:

    def test_creates_integrity_checked_copy(self, tmp_path):
        src = tmp_path / "decisions.db"
        _make_db(src, rows=5)
        dest = tmp_path / "backups"
        out = backup_sqlite(src=str(src), dest_dir=str(dest))
        assert out is not None and out.exists()
        assert out.suffix == ".db" and not out.name.endswith(".tmp")
        con = sqlite3.connect(str(out))
        try:
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 5
        finally:
            con.close()

    def test_missing_source_returns_none(self, tmp_path):
        assert backup_sqlite(
            src=str(tmp_path / "nope.db"), dest_dir=str(tmp_path / "b")
        ) is None

    def test_corrupt_source_raises_and_leaves_no_file(self, tmp_path):
        src = tmp_path / "broken.db"
        src.write_bytes(b"this is not a sqlite database at all" * 10)
        dest = tmp_path / "backups"
        with pytest.raises(RuntimeError):
            backup_sqlite(src=str(src), dest_dir=str(dest))
        assert list_backups(str(dest)) == []
        assert list(dest.glob("*.tmp")) == []

    def test_rotation_keeps_newest(self, tmp_path):
        src = tmp_path / "decisions.db"
        _make_db(src)
        dest = tmp_path / "backups"
        dest.mkdir()
        for i in range(5):
            (dest / f"decisions_2026010{i}_120000.db").write_bytes(b"old")
        out = backup_sqlite(src=str(src), dest_dir=str(dest), keep=3)
        kept = list_backups(str(dest))
        assert len(kept) == 3, f"보관 {len(kept)}개(기대 3개)"
        assert out in kept, "최신 백업이 회전에서 살아남아야 한다"

    def test_stale_tmp_cleaned(self, tmp_path):
        src = tmp_path / "decisions.db"
        _make_db(src)
        dest = tmp_path / "backups"
        dest.mkdir()
        (dest / "decisions_20260101_000000.db.tmp").write_bytes(b"junk")
        backup_sqlite(src=str(src), dest_dir=str(dest))
        assert list(dest.glob("*.tmp")) == [], "이전 실패 임시파일이 남음"

    def test_age_none_then_recent(self, tmp_path):
        dest = tmp_path / "backups"
        assert last_backup_age_hours(str(dest)) is None
        src = tmp_path / "decisions.db"
        _make_db(src)
        backup_sqlite(src=str(src), dest_dir=str(dest))
        age = last_backup_age_hours(str(dest))
        assert age is not None and age < 0.1


class TestSettlementOrder:
    """정산 잡은 일봉 수집(기본 16:30)보다 늦게 실행되어야 한다."""

    def _bootstrap_src(self) -> str:
        p = Path(__file__).resolve().parents[2] / "app" / "bootstrap.py"
        return p.read_text(encoding="utf-8")

    def _job_minutes(self, name: str) -> int:
        src = self._bootstrap_src()
        m = re.search(
            r"CronTrigger\(([^)]*)\)\s*,\s*\n\s*\"" + re.escape(name) + "\"", src
        )
        assert m, f"{name} 잡의 CronTrigger를 찾지 못함"
        args = m.group(1)
        if "ohlcv_hour" in args:  # 설정값 참조 → 스키마 기본값(16:30)
            return 16 * 60 + 30
        hm = re.search(r"hour=(\d+),\s*minute=(\d+)", args)
        assert hm, f"{name} 시각 파싱 실패: {args}"
        return int(hm.group(1)) * 60 + int(hm.group(2))

    def test_ohlcv_time_is_literal_default(self):
        assert self._job_minutes("daily_ohlcv") == 16 * 60 + 30

    def test_paper_settle_after_ohlcv(self):
        assert self._job_minutes("paper_settle") > self._job_minutes("daily_ohlcv")

    def test_calibration_settle_after_ohlcv(self):
        assert self._job_minutes("calibration_settle") > self._job_minutes(
            "daily_ohlcv"
        )

    def test_db_backup_job_registered(self):
        src = self._bootstrap_src()
        assert '"db_backup"' in src
        assert 'self.startup_details["job_count"] = 18' in src
