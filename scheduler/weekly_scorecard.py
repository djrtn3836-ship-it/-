# -*- coding: utf-8 -*-
"""scheduler/weekly_scorecard.py — 주간 신호 성적표 (S136 ⑤ 정식 자동화).

주말 유지보수에서 실행한다:
    ① 알림 신호 vs 무작위 진입(순열검정) — 우리 신호의 최종 성적표
    ② 자체 ML/신뢰도 판별력(분위·Spearman·일자 일관성)
출력은 logs/weekly_scorecard_YYYY-MM-DD.txt 로 저장, 요약 dict 반환.
표본이 쌓일수록 '우리 신호가 무작위보다 나은가'의 판정이 갱신된다.
"""

from __future__ import annotations

import contextlib
import io
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("weekly_scorecard")

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"


def _safe(fn: Callable[[Optional[List[str]]], int], argv: Optional[List[str]] = None) -> int:
    try:
        return int(fn(argv if argv is not None else []))
    except Exception as e:  # 도구 오류가 유지보수를 막지 않게
        logger.error(f"성적표 도구 실패: {e}")
        return -1


def run_scorecard() -> Dict[str, Any]:
    """두 검증 도구 실행 + 로그 저장. {"path":..., "rc":{...}} 반환."""
    from validation.ml_discrimination_test import main as ml_main
    from validation.signal_vs_random_test import main as sig_main

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LOG_DIR / f"weekly_scorecard_{date.today().isoformat()}.txt"
    buf = io.StringIO()
    rc: Dict[str, int] = {}
    with contextlib.redirect_stdout(buf):
        rc["signal_vs_random"] = _safe(sig_main)
        rc["ml_discrimination"] = _safe(ml_main)
    out_path.write_text(f"주간 성적표 {date.today().isoformat()}\n\n{buf.getvalue()}", encoding="utf-8")
    logger.info(f"주간 성적표 저장: {out_path} (rc={rc})")
    return {"path": str(out_path), "rc": rc}
