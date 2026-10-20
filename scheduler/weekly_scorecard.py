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
import json
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


def format_backtest_vs_paper(bt: Dict[str, Any], pf: Dict[str, Any]) -> str:
    """백테스트(S145) vs 모의 실측(S146) 비교 텍스트. 데이터 없으면 빈 문자열."""
    lines = []
    a = ((bt.get("variants") or {}).get("A_all") or {}).get("portfolio") or {}
    if a:
        lines.append(
            f"■ 백테스트 A_전체: CAGR {a.get('cagr')}% · Sharpe {a.get('sharpe')} · MDD {a.get('mdd')}% "
            f"(비용 {float(bt.get('cost_roundtrip', 0)) * 100:.1f}%)"
        )
    if pf:
        lines.append(
            f"■ 모의(실전): 시작 {pf.get('start')} · 누적 {float(pf.get('total_pct', 0)):+.2f}% · "
            f"보유 {pf.get('positions')} · 청산 {pf.get('closed')}(승률 {float(pf.get('win_rate', 0)):.0f}%)"
        )
    elif a:
        lines.append("■ 모의(실전): 첫 실행 대기(16:45)")
    return "\n".join(lines)


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
        try:
            from scheduler.disclosure_settle import disclosure_stats, format_stats

            stats = disclosure_stats()
            if stats:
                print("")
                print(format_stats(stats))
            rc["disclosure_stats"] = 0
        except Exception as e:
            logger.error(f"공시 성적표 실패: {e}")
            rc["disclosure_stats"] = -1
        try:
            from analytics.disclosure_portfolio import load_summary

            bt_path = ROOT / "data" / "disclosure_strategy.json"
            bt = json.loads(bt_path.read_text(encoding="utf-8")) if bt_path.exists() else {}
            txt = format_backtest_vs_paper(bt, load_summary())
            if txt:
                print("")
                print(txt)
            rc["backtest_vs_paper"] = 0
        except Exception as e:
            logger.error(f"백테스트/모의 비교 실패: {e}")
            rc["backtest_vs_paper"] = -1
        try:
            from scheduler.signal_logger import scorecard_text

            print("")
            print(scorecard_text())
            rc["signal_registry"] = 0
        except Exception as e:
            logger.error(f"신호 기록부 성적표 실패: {e}")
            rc["signal_registry"] = -1
    out_path.write_text(f"주간 성적표 {date.today().isoformat()}\n\n{buf.getvalue()}", encoding="utf-8")
    logger.info(f"주간 성적표 저장: {out_path} (rc={rc})")
    return {"path": str(out_path), "rc": rc}
