# -*- coding: utf-8 -*-
"""로그 유지보수 — 회전 아카이브 압축 + 보존기간 초과 삭제.

- `*.log.N`(회전본) 중 5MB 이상 → gzip 압축 후 원본 삭제
- `*.gz` 중 보존기간(기본 90일) 경과 → 삭제
- 현재 사용 중인 `*.log`는 건드리지 않는다(RotatingFileHandler가 관리).
"""
from __future__ import annotations

import gzip
import shutil
import time
from pathlib import Path
from typing import Any, Dict


def run(
    log_dir: Path = Path("logs"),
    *,
    keep_days: int = 90,
    gzip_min_bytes: int = 5 * 1024 * 1024,
    now: float | None = None,
) -> Dict[str, Any]:
    if not log_dir.exists():
        return {"skipped": "no_log_dir"}
    now = time.time() if now is None else now
    gz = deleted = merged = 0
    freed = 0

    for p in sorted(log_dir.glob("*.log.*")):
        if p.suffix == ".gz" or not p.is_file():
            continue
        try:
            size = p.stat().st_size
            if size < gzip_min_bytes:
                continue
            dst = p.with_name(p.name + ".gz")
            with open(p, "rb") as f_in, gzip.open(dst, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            p.unlink()
            gz += 1
            freed += max(0, size - dst.stat().st_size)
        except OSError:
            continue

    for p in log_dir.glob("*.gz"):
        try:
            if now - p.stat().st_mtime > keep_days * 86400:
                freed += p.stat().st_size
                p.unlink()
                deleted += 1
        except OSError:
            continue

    for p in log_dir.glob("*.log.*"):
        try:
            if p.suffix.isdigit() and p.stat().st_size == 0:
                p.unlink()
                merged += 1
        except OSError:
            continue

    bb = log_dir / "blackbox"
    if bb.is_dir():
        sub = run(bb, keep_days=keep_days, gzip_min_bytes=gzip_min_bytes, now=now)
        gz += sub.get("gzip", 0)
        deleted += sub.get("deleted_gz", 0)
        freed += int(float(sub.get("freed_mb", 0)) * 1024 * 1024)

    return {
        "gzip": gz,
        "deleted_gz": deleted,
        "removed_empty": merged,
        "freed_mb": round(freed / (1024 * 1024), 2),
    }


def main() -> int:
    print(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
