"""SQLite 안전 백업 (2026-10-10 Session 103).

왜 필요한가:
- decisions.db는 학습 데이터(결정·결과)의 전부인데 자동 백업이 없었다
  (수동 ZIP뿐 → 잊으면 유실).
- 실행 중 파일을 단순 복사하면 WAL/진행 중 쓰기와 섞여 깨진 사본이 될 수 있다.
  → sqlite3 백업 API(온라인 백업)로 **일관성 있는 스냅샷**을 뜬다.
- PRAGMA integrity_check를 통과한 사본만 남긴다(깨진 백업은 백업이 아니다).
- 보관 개수를 넘으면 오래된 것부터 삭제한다(디스크 보호).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

DEFAULT_PREFIX = "decisions"


def _db_path() -> Path:
    return Path(os.getenv("SQLITE_DB_PATH", "data/decisions.db"))


def _backup_dir() -> Path:
    return Path(os.getenv("BACKUP_DIR", "backups"))


def _keep_default() -> int:
    try:
        return max(1, int(os.getenv("BACKUP_KEEP", "7")))
    except ValueError:
        return 7


def backup_sqlite(
    src: Optional[str] = None,
    dest_dir: Optional[str] = None,
    keep: Optional[int] = None,
    prefix: str = DEFAULT_PREFIX,
) -> Optional[Path]:
    """일관성 있는 SQLite 백업을 만들고 보관 개수를 관리한다.

    Returns:
        백업 파일 경로(성공) / None(원본 없음)

    Raises:
        RuntimeError: 복사·무결성 검사 실패(사본은 남기지 않는다)
    """
    if keep is None:
        keep = _keep_default()
    src_path = Path(src) if src else _db_path()
    dest = Path(dest_dir) if dest_dir else _backup_dir()
    if not src_path.exists():
        return None

    dest.mkdir(parents=True, exist_ok=True)

    # 이전 실패로 남은 임시 파일 정리
    for stale in dest.glob(f"{prefix}_*.db.tmp"):
        try:
            stale.unlink()
        except OSError:
            pass

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp = dest / f"{prefix}_{stamp}.db.tmp"
    final = dest / f"{prefix}_{stamp}.db"

    try:
        src_con = sqlite3.connect(str(src_path))
        try:
            dst_con = sqlite3.connect(str(tmp))
            try:
                src_con.backup(dst_con)
            finally:
                dst_con.close()
        finally:
            src_con.close()
    except sqlite3.Error as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"백업 복사 실패: {e}") from e

    try:
        chk = sqlite3.connect(str(tmp))
        try:
            row = chk.execute("PRAGMA integrity_check").fetchone()
        finally:
            chk.close()
    except sqlite3.Error as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"백업 검사 실패: {e}") from e

    if not row or row[0] != "ok":
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"백업 무결성 실패: {row}")

    tmp.replace(final)

    backups = sorted(dest.glob(f"{prefix}_*.db"))
    if keep > 0:
        for old in backups[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
    return final


def list_backups(
    dest_dir: Optional[str] = None, prefix: str = DEFAULT_PREFIX
) -> List[Path]:
    """보관 중인 백업 목록(오래된 순)."""
    dest = Path(dest_dir) if dest_dir else _backup_dir()
    return sorted(dest.glob(f"{prefix}_*.db"))


def last_backup_age_hours(
    dest_dir: Optional[str] = None, prefix: str = DEFAULT_PREFIX
) -> Optional[float]:
    """가장 최근 백업의 경과 시간(시간). 백업이 하나도 없으면 None."""
    files = list_backups(dest_dir, prefix)
    if not files:
        return None
    newest = max(f.stat().st_mtime for f in files)
    return (datetime.now().timestamp() - newest) / 3600.0
