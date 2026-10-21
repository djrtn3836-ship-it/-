# -*- coding: utf-8 -*-
"""scheduler/weekend_maintenance.py — 비거래일(주말) 1회성 유지보수 러너.

배경 (2026-10-17, 사용자 승인):
    앱은 비거래일 신규 기동을 차단한다(app/main.py 가드). 금요일 인스턴스가
    주말 내내 살아있으면 주말 잡(토 유니버스 갱신·일 DB 유지보수/데이터 점검)이
    정상 실행되지만, **재부팅으로 금요일 인스턴스가 죽으면 주말 잡이 전부
    미실행**된다(실측 사건).
    → 비거래일 기동 시 1회성으로 주말 잡을 실행하고 종료하는 러너를 추가한다.
    (주말 내내 인스턴스를 띄우지 않는다 — 월요일 시장 감시 설정이 주말 환경에
     묶여 굳는 위험 회피. 월요일은 평소처럼 새 인스턴스가 기동한다.)

설계:
    - 인스턴스 락 획득 **후**에만 실행(app/main.py 순서) — 살아있는 인스턴스가
      있으면 그 인스턴스가 주말 잡을 담당(중복 실행 없음).
    - 토요일: 유니버스 갱신(주기 잡). CSV가 6일+ 낡았으면 일요일에도 갱신.
    - 일요일: DB 유지보수(PRAGMA optimize — VACUUM 없음) + 데이터 준비도 점검.
    - S133: 폭 역사 갱신(breadth_history)을 매 유지보수 실행 — 브리핑/대시보드
      '역사 백분위' 표시의 신선도 유지.
    - S136: 주간 성적표(weekly_scorecard) — 알림 vs 무작위·ML 판별력 자동 축적.
    - 안전모드(TEST_MODE 등)면 전부 건너뜀(프로덕션 DB 보호).
    - 모든 잡 함수 주입 가능(오프라인 테스트).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.logger import setup_logger

logger = setup_logger("weekend_maintenance")

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_CSV = ROOT / "data" / "krx_universe.csv"
CSV_STALE_DAYS = 6.0


def _csv_age_days(csv_path: Path, now: datetime) -> float:
    try:
        mtime = datetime.fromtimestamp(csv_path.stat().st_mtime)
    except OSError:
        return 999.0
    return (now - mtime).total_seconds() / 86400.0


async def run_maintenance(
    *,
    now_fn: Callable[[], datetime] = datetime.now,
    refresh_fn: Optional[Callable[[], Any]] = None,
    readiness_fn: Optional[Callable[[], Any]] = None,
    maintenance_fn: Optional[Callable[..., Any]] = None,
    breadth_fn: Optional[Callable[[], Any]] = None,
    scorecard_fn: Optional[Callable[[], Any]] = None,
    check_fn: Optional[Callable[[], Any]] = None,
    log_fn: Optional[Callable[[], Any]] = None,
    csv_path: Path = UNIVERSE_CSV,
) -> Dict[str, Any]:
    """비거래일 1회성 유지보수 실행. {"day":요일, "jobs":{이름:결과}} 반환."""
    from core.runtime_mode import get_runtime_mode

    if get_runtime_mode().is_safe:
        logger.info("안전모드 — 주말 유지보수 건너뜀")
        return {"skipped": "safe_mode"}

    now = now_fn()
    dow = now.weekday()  # 5=토, 6=일
    result: Dict[str, Any] = {"day": dow, "jobs": {}}

    if refresh_fn is None:
        from scheduler.universe_fetcher import scheduled_universe_refresh

        refresh_fn = scheduled_universe_refresh
    if readiness_fn is None:
        from scheduler.data_readiness_monitor import scheduled_data_readiness_check

        readiness_fn = scheduled_data_readiness_check
    if maintenance_fn is None:
        from core.db_maintenance import maintain_sqlite

        maintenance_fn = maintain_sqlite
    if breadth_fn is None:
        from analytics.market_breadth import refresh_history

        breadth_fn = refresh_history
    if check_fn is None:

        def _default_self_check() -> Dict[str, Any]:
            import subprocess
            import sys

            root = Path(__file__).resolve().parents[1]
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", "scripts/self_check.py"],
                cwd=str(root), capture_output=True, text=True, encoding="utf-8",
            )
            if proc.returncode != 0:
                logger.error(f"자체 점검 실패(rc={proc.returncode}): {(proc.stdout or '')[-300:]}")
            return {"rc": proc.returncode, "out": (proc.stdout or "").strip()[-400:]}

        check_fn = _default_self_check

    if log_fn is None:
        from scripts.log_maintenance import run as _log_run

        _root = Path(__file__).resolve().parents[1]
        log_fn = lambda: _log_run(_root / "logs")

    if scorecard_fn is None:
        from scheduler.weekly_scorecard import run_scorecard

        scorecard_fn = run_scorecard

    async def _safe(name: str, fn: Callable[[], Any]) -> None:
        try:
            r = fn()
            if asyncio.iscoroutine(r):
                r = await r
            result["jobs"][name] = r if isinstance(r, (dict, int, float, str)) else str(r)[:80]
            logger.info(f"주말 유지보수 [{name}] 완료: {str(r)[:120]}")
        except Exception as e:
            result["jobs"][name] = f"error: {str(e)[:120]}"
            logger.error(f"주말 유지보수 [{name}] 실패: {e}")

    need_refresh = (dow == 5) or _csv_age_days(csv_path, now) >= CSV_STALE_DAYS

    if need_refresh:
        await _safe("universe_refresh", refresh_fn)
    if dow == 6 or _is_holiday_weekday(now):
        await _safe("db_maintenance", lambda: asyncio.to_thread(maintenance_fn))
        await _safe("data_readiness", readiness_fn)
    # S133: 폭 역사 갱신(브리핑/대시보드 '역사 백분위' 신선도 유지) — 매 유지보수 실행
    await _safe("breadth_history", lambda: asyncio.to_thread(breadth_fn))
    # S136: 주간 신호 성적표(알림 vs 무작위 · ML 판별력) — 판정 축적
    await _safe("weekly_scorecard", lambda: asyncio.to_thread(scorecard_fn))
    # S149: 데이터·상태 자체 점검(JSONL 무결성·포트폴리오 회계·job_count·BOM)
    await _safe("self_check", lambda: asyncio.to_thread(check_fn))
    # S154: 로그 유지보수(회전본 압축·보존기간 초과 삭제) — 현재 로그는 건드리지 않음
    await _safe("log_maintenance", lambda: asyncio.to_thread(log_fn))

    logger.info(f"주말 유지보수 종료: {result}")
    return result


def _is_holiday_weekday(now: datetime) -> bool:
    """평일 공휴일(비거래일이지만 토·일 아님) — 점검 잡을 함께 실행."""
    return now.weekday() < 5
