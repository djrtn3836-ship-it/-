# -*- coding: utf-8 -*-
"""JSONL 기록 파일 크기 상한·회전 유틸 (2026-10-09 사고 대응).

🔴 사고: `logs/calibration_predictions.jsonl` 1.2GB, `logs/shadow_records.jsonl` 818MB까지
   무한 증가(하루). 로거 파일은 `GzipRotatingFileHandler`(10MB×10)로 회전되지만,
   **JSONL 기록 파일은 회전 정책이 전혀 없었다.**

정책:
    - 20MB 도달 시 `.1.gz`로 gzip 회전, 최대 3개 보관(오래된 것 자동 삭제)
    - 상한·보관 수는 환경변수로 조정: JSONL_MAX_BYTES / JSONL_BACKUP_COUNT
    - 회전 실패는 무시(기록 경로는 프로덕션 판단에 영향을 주지 않아야 함)
"""
from __future__ import annotations

import gzip
import os
import shutil
from pathlib import Path
from typing import Union

DEFAULT_MAX_BYTES = int(os.getenv("JSONL_MAX_BYTES", str(20 * 1024 * 1024)))
DEFAULT_BACKUP_COUNT = int(os.getenv("JSONL_BACKUP_COUNT", "3"))


def _backup_path(path: Path, index: int) -> Path:
    return path.with_suffix(path.suffix + f".{index}.gz")


def rotate_if_needed(
    path: Union[str, Path],
    max_bytes: int = DEFAULT_MAX_BYTES,
    backups: int = DEFAULT_BACKUP_COUNT,
) -> bool:
    """파일이 상한을 넘으면 회전한다. 회전했으면 True.

    `.N.gz` 번호가 클수록 오래된 파일이며, `backups`를 넘는 파일은 삭제된다.
    """
    p = Path(path)
    try:
        if not p.exists() or p.stat().st_size < max_bytes:
            return False
    except OSError:
        return False

    try:
        oldest = _backup_path(p, backups)
        if oldest.exists():
            oldest.unlink()
        for i in range(backups - 1, 0, -1):
            src = _backup_path(p, i)
            if src.exists():
                shutil.move(str(src), str(_backup_path(p, i + 1)))
        with open(p, "rb") as fin, gzip.open(_backup_path(p, 1), "wb") as fout:
            shutil.copyfileobj(fin, fout)
        p.unlink()
        return True
    except OSError:
        return False
