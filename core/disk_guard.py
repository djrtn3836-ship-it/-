"""디스크 여유 공간 감시 (2026-10-10 Session 105).

왜 필요한가:
- 이 앱은 로그·DB·JSONL을 계속 쓴다. 디스크가 가득 차면 SQLite 쓰기가
  실패하는데 그 실패는 **조용하다**(결정 저장 실패 = 학습 데이터 손실).
- 과거 사고: 로그 폭주로 3.9GB가 쌓여 디스크를 소진할 뻔했다.
→ 여유가 임계 미만이면 텔레그램으로 경고한다(쿨다운은 호출측 책임).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional


def disk_status(path: Optional[str] = None) -> Dict[str, Any]:
    """대상 경로의 디스크 사용량."""
    target = path or os.getenv("DISK_GUARD_PATH") or "."
    u = shutil.disk_usage(target)
    return {
        "path": str(Path(target).resolve()),
        "total_gb": round(u.total / 2**30, 1),
        "used_gb": round(u.used / 2**30, 1),
        "free_gb": round(u.free / 2**30, 1),
        "free_pct": round(u.free / u.total * 100, 1) if u.total else 0.0,
    }


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def check_disk(
    path: Optional[str] = None,
    min_free_gb: Optional[float] = None,
    min_free_pct: Optional[float] = None,
) -> Optional[str]:
    """여유가 부족하면 경고 메시지, 충분하면 None."""
    if min_free_gb is None:
        min_free_gb = _env_float("DISK_MIN_FREE_GB", 3.0)
    if min_free_pct is None:
        min_free_pct = _env_float("DISK_MIN_FREE_PCT", 5.0)

    st = disk_status(path)
    reasons = []
    if st["free_gb"] < min_free_gb:
        reasons.append(f"여유 {st['free_gb']}GB < 기준 {min_free_gb}GB")
    if st["free_pct"] < min_free_pct:
        reasons.append(f"여유 {st['free_pct']}% < 기준 {min_free_pct}%")
    if not reasons:
        return None

    return (
        f"💾 <b>디스크 여유 부족</b>\n"
        f"📌 {', '.join(reasons)}\n"
        f"📂 {st['path']}\n"
        f"📊 {st['used_gb']}GB 사용 / 여유 {st['free_gb']}GB ({st['free_pct']}%)\n"
        f"⚠️ 가득 차면 DB 저장이 조용히 실패합니다 — 정리 필요"
    )
