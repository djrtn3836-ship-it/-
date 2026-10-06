"""스케줄러 잡이 메인 이벤트 루프에서 실행되는지 검증.

2026-10-06 실사고: APScheduler가 잡을 스레드에서 실행 →
asyncio.get_running_loop() 실패 → asyncio.run()이 새 루프 생성 →
메인 루프에 묶인 aiohttp 세션 사용 시
"Timeout context manager should be used inside a task"로 196건 크래시.
"""
import asyncio
import sys
import threading
from pathlib import Path

import pytest
from apscheduler.triggers.cron import CronTrigger

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.scheduler import SchedulerManager  # noqa: E402


@pytest.mark.asyncio
async def test_capture_loop_records_running_loop():
    mgr = SchedulerManager()
    mgr._capture_loop()
    assert mgr._main_loop is asyncio.get_running_loop()


@pytest.mark.asyncio
async def test_job_from_thread_runs_in_main_loop():
    """스레드에서 트리거된 잡이 메인 루프로 복귀해 실행되어야 한다."""
    mgr = SchedulerManager()
    mgr.start()
    try:
        done = asyncio.Event()
        captured: dict = {}

        async def job() -> None:
            captured["loop"] = asyncio.get_running_loop()
            captured["task"] = asyncio.current_task()
            done.set()

        mgr.add_job_with_retry(job, CronTrigger(hour=3, minute=0), "loop_test")
        wrapper = mgr.scheduler.get_job("loop_test").func

        # APScheduler의 스레드 실행을 재현
        t = threading.Thread(target=wrapper)
        t.start()
        t.join()

        await asyncio.wait_for(done.wait(), timeout=15)
        assert captured["loop"] is asyncio.get_running_loop()
        assert captured["task"] is not None  # 태스크 컨텍스트 유지(Timeout CM 조건)
    finally:
        try:
            mgr.shutdown()
        except Exception:
            pass


def test_no_main_loop_falls_back_to_asyncio_run():
    """메인 루프가 아예 없는 환경(동기 컨텍스트)에서는 기존 asyncio.run 폴백 유지."""
    mgr = SchedulerManager()
    assert mgr._main_loop is None
    seen: dict = {}

    async def job() -> None:
        seen["ran"] = True

    mgr.add_job_with_retry(job, CronTrigger(hour=3, minute=0), "fallback_test")
    wrapper = mgr.scheduler.get_job("fallback_test").func
    # 메인 루프가 없는 스레드에서 실행 → asyncio.run 폴백
    t = threading.Thread(target=wrapper)
    t.start()
    t.join()
    assert seen.get("ran") is True
