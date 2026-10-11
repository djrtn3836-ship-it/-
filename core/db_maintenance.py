"""SQLite 정기 유지보수 (2026-10-10 Session 105).

- `PRAGMA optimize`: 쿼리 플래너 통계 갱신(가볍다 — SQLite 공식 권장).
- 테이블별 행수·파일 크기·무결성을 보고해 성장을 관측한다(로그).
- VACUUM은 선택(기본 off): 파일을 재작성하므로 시간·여유 공간이 필요하다.
  수동 실행: `python -m core.db_maintenance --vacuum`
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def _db_path() -> Path:
    return Path(os.getenv("SQLITE_DB_PATH", "data/decisions.db"))


def maintain_sqlite(
    db_path: Optional[str] = None, vacuum: bool = False
) -> Dict[str, Any]:
    """PRAGMA optimize(+선택 VACUUM) + 통계 수집."""
    p = Path(db_path) if db_path else _db_path()
    if not p.exists():
        return {"status": "no_db", "path": str(p)}

    size_before = p.stat().st_size
    con = sqlite3.connect(str(p))
    try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts: Dict[str, int] = {}
        for t in tables:
            try:
                counts[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except sqlite3.Error:
                counts[t] = -1
        con.commit()
        con.execute("PRAGMA optimize")
        if vacuum:
            con.execute("VACUUM")
        integrity = con.execute("PRAGMA integrity_check").fetchone()
    finally:
        con.close()

    return {
        "status": "ok",
        "path": str(p),
        "size_before_mb": round(size_before / 1048576, 1),
        "size_mb": round(p.stat().st_size / 1048576, 1),
        "integrity": (integrity[0] if integrity else "unknown"),
        "tables": counts,
        "vacuum": vacuum,
    }


def _main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="SQLite 유지보수")
    parser.add_argument("--db", default=None, help="DB 경로(기본: SQLITE_DB_PATH)")
    parser.add_argument("--vacuum", action="store_true", help="VACUUM 실행(느림)")
    args = parser.parse_args()

    r = maintain_sqlite(args.db, vacuum=args.vacuum)
    if r.get("status") != "ok":
        print(f"건너뜀: {r}")
        return 1
    print(f"DB: {r['path']}")
    print(f"크기: {r['size_before_mb']}MB → {r['size_mb']}MB")
    print(f"무결성: {r['integrity']}")
    for k, v in sorted(r["tables"].items(), key=lambda kv: kv[1], reverse=True):
        print(f"  {k}: {v:,}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
