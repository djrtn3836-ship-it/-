# -*- coding: utf-8 -*-
"""scheduler/data_readiness_monitor.py - 데이터 축적 게이트 (P7).

배경
----
P5/P6 검증에서 **가격기반 팩터는 유의한 알파가 없음**이 확인됐다.
차기 유일한 미개척 경로는 감성/공시/ML 같은 **비가격 팩터**인데,
이를 백테스트하려면 `decisions`/`decision_outcomes` 데이터가 쌓여 있어야 한다.
(2026-10-01 실측: 둘 다 0행 → 검증 불가)

역할
----
- 주기적으로 학습 데이터 누적량을 점검하고 **준비도(readiness)** 를 판정한다.
- 임계 도달 시 **최초 1회만** 텔레그램으로 알린다(매주 스팸 방지 → 상태 파일 기록).
- 도달 후에는 ML 팩터 검증을 수행할 수 있음을 로그/알림으로 안내한다.

사용:
    python -m scheduler.data_readiness_monitor
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from core.logger import setup_logger

logger = setup_logger("data_readiness")

# ML/감성 팩터 검증에 필요한 최소 표본 (경험적 기준)
MIN_DECISIONS = 500
MIN_OUTCOMES = 300

# P12-5: 테스트 격리용
STATE_PATH = Path(
    os.getenv("READINESS_STATE_PATH",
              str(Path(__file__).parent.parent / "data" / "readiness_state.json"))
)


async def count_rows(db: Any, table: str) -> int:
    """테이블 행 수(없으면 0)."""
    try:
        rows = await db._execute_read(f"SELECT COUNT(*) AS n FROM {table}")
        return int(rows[0]["n"]) if rows else 0
    except Exception as e:
        logger.debug(f"{table} 카운트 실패(무시): {e}")
        return 0


async def check_data_readiness(db: Any) -> Dict[str, Any]:
    """학습 데이터 준비도를 판정한다.

    Returns:
        {
          "decisions": int, "outcomes": int, "ohlcv": int, "evaluated_paper": int,
          "ml_ready": bool, "progress": float, "message": str,
        }
    """
    decisions = await count_rows(db, "decisions")
    outcomes = await count_rows(db, "decision_outcomes")
    ohlcv = await count_rows(db, "ohlcv")
    evaluated_paper = 0
    try:
        rows = await db._execute_read(
            "SELECT COUNT(*) AS n FROM momentum_paper WHERE evaluated_at IS NOT NULL"
        )
        evaluated_paper = int(rows[0]["n"]) if rows else 0
    except Exception as e:
        logger.debug(f"momentum_paper 카운트 실패(무시): {e}")

    ml_ready = decisions >= MIN_DECISIONS and outcomes >= MIN_OUTCOMES
    progress = min(
        1.0,
        min(decisions / MIN_DECISIONS, outcomes / MIN_OUTCOMES) if MIN_OUTCOMES else 0.0,
    )

    if ml_ready:
        message = (
            f"✅ ML/감성 팩터 검증 가능 — decisions {decisions}건, outcomes {outcomes}건 "
            f"(기준 {MIN_DECISIONS}/{MIN_OUTCOMES})"
        )
    else:
        message = (
            f"⏳ 데이터 축적 중 — decisions {decisions}/{MIN_DECISIONS}건, "
            f"outcomes {outcomes}/{MIN_OUTCOMES}건 ({progress:.0%})"
        )

    return {
        "decisions": decisions,
        "outcomes": outcomes,
        "ohlcv": ohlcv,
        "evaluated_paper": evaluated_paper,
        "ml_ready": ml_ready,
        "progress": round(progress, 4),
        "message": message,
    }


def _load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug(f"준비도 상태 파일 로드 실패(무시): {e}")
    return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"준비도 상태 파일 저장 실패(무시): {e}")


async def scheduled_data_readiness_check() -> Dict[str, Any]:
    """스케줄러 진입점(주 1회). 임계 최초 도달 시에만 텔레그램 알림."""
    from data.db_manager import DatabaseManager

    db = DatabaseManager()
    try:
        result = await check_data_readiness(db)
    except Exception as e:
        logger.error(f"데이터 준비도 점검 실패: {e}")
        return {}
    finally:
        try:
            await db.close()
        except Exception:
            pass

    logger.info(result.get("message", ""))

    state = _load_state()
    already_notified = bool(state.get("ml_ready_notified"))
    if result.get("ml_ready") and not already_notified:
        state["ml_ready_notified"] = True
        state["notified_at"] = datetime.now().isoformat()
        _save_state(state)
        await _notify_ready(result)
    elif result.get("ml_ready"):
        state["last_checked_at"] = datetime.now().isoformat()
        _save_state(state)

    return result


async def _notify_ready(result: Dict[str, Any]) -> None:
    try:
        from report.telegram_sender import TelegramSender

        text = (
            "📊 <b>데이터 축적 목표 도달</b>\n"
            f"• decisions: {result['decisions']}건\n"
            f"• outcomes: {result['outcomes']}건\n"
            "이제 <b>비가격 팩터(ML/감성)</b> 횡단면 검증이 가능합니다.\n"
            "<i>P7 로드맵: `python -m validation.momentum_backtest --scan` 참고</i>"
        )
        await TelegramSender().send_raw(text)
    except Exception as e:
        logger.warning(f"준비도 알림 전송 실패: {e}")


async def _main() -> int:
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    from data.db_manager import DatabaseManager

    db = DatabaseManager()
    try:
        result = await check_data_readiness(db)
    finally:
        await db.close()

    print(result.get("message", ""))
    print(
        f"  ohlcv {result.get('ohlcv', 0):,}행 | 모의평가 {result.get('evaluated_paper', 0)}건 | "
        f"진행률 {result.get('progress', 0):.0%}"
    )
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(_main()))
