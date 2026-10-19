# -*- coding: utf-8 -*-
"""scripts/mkzip.py — stock_analyzer 백업 ZIP 생성기 (레포 관리, 2026-10-19).

배경: 기존 스크립트가 %TEMP%에 있다가 삭제됨 + 실행 중 파일 잠금 문제 2건:
  - .NET CreateEntryFromFile은 소스를 FileShare.Read로 열어 **쓰기 중인 로그를 못 읽음**.
  - PowerShell 함수 경유 시 NullReference 재현(원인 미규명) — 우회가 아니라 재구현.
파이썬의 open(deny-none 공유)은 실행 중 로그/파일을 읽을 수 있음(감사 스크립트로 실증).
DB는 sqlite3 online backup으로 **일관 스냅샷**을 떠서 포함(WAL 내용 병합, -wal/-shm 제외).

사용: python scripts/mkzip.py
출력: C:\\Users\\hdw38\\Desktop\\stock_analyzer.zip
"""

from __future__ import annotations

import os
import sqlite3
import sys
import zipfile
from pathlib import Path

SRC = Path(r"C:\Users\hdw38\Desktop\stock_analyzer")
DST = Path(r"C:\Users\hdw38\Desktop\stock_analyzer.zip")
SKIP_DIRS = {"backups", "__pycache__", ".pytest_cache", ".git", "node_modules", ".mypy_cache"}
skip_dirs = SKIP_DIRS


def main() -> int:
    if not SRC.exists():
        print("SRC_MISSING")
        return 1

    src_db = SRC / "data" / "decisions.db"
    snap = Path(os.environ.get("TEMP", ".")) / "sa_zip_stage.db"
    snap_ok = False
    if src_db.exists():
        try:
            s = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
            d = sqlite3.connect(str(snap))
            s.backup(d)
            d.close()
            s.close()
            snap_ok = True
        except Exception as e:
            print(f"DB_SNAPSHOT_FAILED: {e}")

    if DST.exists():
        DST.unlink()

    failed = []
    count = 0
    with zipfile.ZipFile(DST, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, dirs, files in os.walk(SRC):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                fp = Path(root) / f
                rel = fp.relative_to(SRC).as_posix()
                if f == "decisions.db":
                    continue
                if rel.startswith("data/decisions.db-"):
                    continue
                try:
                    z.write(fp, rel)
                    count += 1
                except Exception as e:
                    failed.append(f"{rel}({str(e)[:40]})")
        if snap_ok:
            z.write(snap, "data/decisions.db")
            count += 1

    if snap.exists():
        snap.unlink()

    size_mb = round(DST.stat().st_size / 1048576, 1)
    print(f"ZIP_OK entries={count} failed={len(failed)} sizeMB={size_mb}")
    if failed:
        print("FAILED_FILES: " + ", ".join(failed[:20]))
    return 0 if count > 0 else 2


if __name__ == "__main__":
    sys.exit(main())
