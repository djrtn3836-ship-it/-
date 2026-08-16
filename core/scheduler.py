"""
core/scheduler.py - v2.0 (재시도 래퍼 활성화 + 예외 처리)
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from core.logger import setup_logger
from core.blackbox_logger import log_error
import asyncio
import functools

logger = setup_logger("scheduler")

class SchedulerManager:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
        self._jobs = []

    def start(self):
        self.scheduler.start()
        logger.info("⏰ Scheduler started")

    def shutdown(self):
        self.scheduler.shutdown()
        logger.info("⏰ Scheduler shutdown")

    # 🔥 R-02 해결: 안전한 재시도 래퍼
    def add_job_with_retry(self, coro_func, trigger, job_id, max_retries=3, retry_delay=5, *args, **kwargs):
        """
        재시도 로직이 포함된 스케줄 작업 등록
        """
        async def _wrapped_coro():
            for attempt in range(max_retries + 1):
                try:
                    await coro_func(*args, **kwargs)
                    return
                except Exception as e:
                    if attempt < max_retries:
                        logger.warning(f"⚠️ {job_id} 실패 ({attempt+1}/{max_retries+1}), {retry_delay}초 후 재시도: {e}")
                        await asyncio.sleep(retry_delay * (attempt + 1))
                    else:
                        log_error(f"스케줄 작업 최종 실패: {job_id}", e)

        def _wrapper():
            try:
                loop = asyncio.get_running_loop()
                asyncio.create_task(_wrapped_coro())
            except RuntimeError:
                asyncio.run(_wrapped_coro())

        self.scheduler.add_job(_wrapper, trigger=trigger, id=job_id, replace_existing=True)
        logger.info(f"📅 스케줄 등록: {job_id} (재시도 {max_retries}회)")

    # 기존 편의 메서드 (하위 호환성)
    def add_daily_report(self, coro_func, hour=7, minute=0):
        trigger = CronTrigger(hour=hour, minute=minute, timezone="Asia/Seoul")
        self.add_job_with_retry(coro_func, trigger, "daily_report")

    def add_feedback_learning(self, coro_func, hour=17, minute=0):
        trigger = CronTrigger(hour=hour, minute=minute, timezone="Asia/Seoul")
        self.add_job_with_retry(coro_func, trigger, "feedback_learning")