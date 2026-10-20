# -*- coding: utf-8 -*-
"""scheduler/backfill_supervisor.py — 수급 백필 자동 감시·재개 (2026-10-17, 사용자 요청).

배경:
    09:41 백필이 no_data 오판으로 중단된 뒤 3시간 방치됐다(사용자 질문으로 발견).
    요청: "요청하지 않아도 시간을 정해 자동으로 진행" → 30분 주기 예약작업
    (stock_analyzer_backfill)이 이 감시자를 기동하고, 감시자는:
      1) 소켓 락(포트 47654) — 이미 감시자/백필이 실행 중이면 즉시 종료(중복 0)
      2) 완료(ticker_idx >= 유니버스 수)면: 완료 알림 1회 + 예약작업 자동 해제 + 종료
      3) 아니면 백필 실행 → 중단(rc=2)이면 10분 후 재시도(기동당 최대 3회)
         — 3회 실패로 끝나도 30분 뒤 예약작업이 다시 기동(무한 복구)
    예약작업은 완료 시 자동 삭제되므로 무기한 방치되지 않는다.

안전:
    - 프로덕션 백필은 이 감시자를 통해서만 기동한다(run_backfill_auto.bat → 감시자).
      감시자가 락을 쥐고 있는 동안 예약작업이 중복 기동해도 즉시 종료된다.
    - 실행/알림/작업해제/시계 전부 주입 가능(오프라인 테스트).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from core.logger import setup_logger

logger = setup_logger("backfill_supervisor")

ROOT = Path(__file__).resolve().parents[1]
DONE_FLAG = ROOT / "logs" / "supply_backfill_done.flag"
TASK_NAME = "stock_analyzer_backfill"
LOCK_PORT = int(os.getenv("BACKFILL_LOCK_PORT", "47654"))
RETRY_WAIT_SEC = 600.0
MAX_TRIES = 3


def acquire_supervisor_lock(port: int = LOCK_PORT) -> socket.socket:
    """감시자 소켓 락. 이미 점유 중이면 SystemExit(0)(중복 기동 아님)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 🔴 Windows 주의: SO_REUSEADDR을 켜면 이미 바인드된 포트에도 중복 바인드가
    #   성공해 락이 무력화된다(실측 — 테스트가 잡음). 켜지 않으면 두 번째 바인드가
    #   WSAEADDRINUSE로 실패해 정상 차단된다.
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        logger.info(f"백필 감시자 락 획득(포트 {port})")
        return s
    except OSError:
        s.close()
        logger.info("이미 백필 감시자가 실행 중 — 중복 기동 종료")
        raise SystemExit(0) from None


def _read_ticker_idx(checkpoint_path: Path) -> int:
    try:
        with open(checkpoint_path, encoding="utf-8") as f:
            return int(json.load(f).get("ticker_idx") or 0)
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        return 0


def _default_total() -> int:
    try:
        from scheduler.supply_backfill import _load_universe

        return len(_load_universe())
    except Exception as e:
        logger.warning(f"유니버스 로드 실패(진행 판정 보류): {e}")
        return 0


def _default_runner() -> Any:
    from scheduler.supply_backfill import _main as backfill_main

    return backfill_main([])


async def _notify_telegram(text: str) -> None:
    try:
        from core.runtime_mode import get_runtime_mode

        if not get_runtime_mode().telegram_enabled:
            logger.info(f"텔레그램 비활성 — 알림 생략: {text[:60]}")
            return
        from report.telegram_sender import TelegramSender

        await TelegramSender().send_raw(text)
    except Exception as e:
        logger.warning(f"알림 전송 실패(무시): {e}")


def _default_task_deleter() -> bool:
    try:
        r = subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True, text=True, timeout=30,
        )
        ok = r.returncode == 0
        logger.info(f"예약작업 해제({'성공' if ok else '실패'}): {(r.stdout or r.stderr or '')[:100]}")
        return ok
    except Exception as e:
        logger.warning(f"예약작업 해제 실패(무시): {e}")
        return False


async def supervise(
    *,
    runner: Optional[Callable[[], Any]] = None,
    notifier: Optional[Callable[[str], Any]] = None,
    task_deleter: Optional[Callable[[], Any]] = None,
    checkpoint_path: Optional[Path] = None,
    done_flag: Path = DONE_FLAG,
    total_tickers_fn: Optional[Callable[[], int]] = None,
    retry_wait: float = RETRY_WAIT_SEC,
    max_tries: int = MAX_TRIES,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> int:
    """감시 1회 기동(내부 재시도 포함). 0=완료, 2=중단 지속."""
    if checkpoint_path is None:
        from scheduler.supply_backfill import CHECKPOINT_PATH

        checkpoint_path = CHECKPOINT_PATH
    runner = runner or _default_runner
    notifier = notifier or _notify_telegram
    task_deleter = task_deleter or _default_task_deleter
    total = total_tickers_fn() if total_tickers_fn else _default_total()

    async def _notify(text: str) -> None:
        r = notifier(text)
        if asyncio.iscoroutine(r):
            await r

    async def _delete_task() -> None:
        r = task_deleter()
        if asyncio.iscoroutine(r):
            await r

    def _mark_done() -> bool:
        """완료 플래그 기록. 이미 있으면 False(중복 알림 방지)."""
        if done_flag.exists():
            return False
        try:
            done_flag.parent.mkdir(parents=True, exist_ok=True)
            done_flag.write_text(
                f"done {datetime.now().isoformat(timespec='seconds')}\n", encoding="utf-8",
            )
        except OSError:
            pass
        return True

    if total > 0 and _read_ticker_idx(checkpoint_path) >= total:
        if _mark_done():
            await _notify(
                "✅ <b>수급 5년 백필 완료</b>\n"
                "감시 작업(예약작업)을 자동 해제했습니다. 검증은 후속 단계입니다."
            )
        await _delete_task()
        logger.info("백필 완료 상태 확인 — 종료")
        return 0

    for attempt in range(1, max_tries + 1):
        try:
            rc = await runner()
        except Exception as e:
            logger.error(f"백필 실행 예외: {e}")
            rc = 2
        rc = int(rc or 0)
        if rc == 0:
            if _mark_done():
                await _notify("✅ <b>수급 5년 백필 완료</b>\n감시 작업을 자동 해제했습니다.")
            await _delete_task()
            return 0
        logger.warning(
            f"백필 중단(rc={rc}) — {attempt}/{max_tries}회차. "
            f"{retry_wait / 60:.0f}분 후 재시도 예정"
        )
        if attempt < max_tries:
            await sleep(retry_wait)

    await _notify(
        f"⚠️ <b>수급 백필 {max_tries}회 연속 중단</b>\n"
        "30분 후 예약작업이 자동 재시도합니다. 반복되면 API 제한 확인 필요."
    )
    return 2


async def _main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="수급 백필 자동 감시·재개")
    parser.parse_args(argv)
    lock = acquire_supervisor_lock()
    try:
        return await supervise()
    finally:
        try:
            lock.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
