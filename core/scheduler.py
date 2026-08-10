"""
core/scheduler.py - Scheduler Manager with Retry Logic (B)
"""
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from core.logger import setup_logger

logger = setup_logger("scheduler")

class SchedulerManager:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self._jobs = []

    async def _retry_wrapper(self, func, max_retries=3, retry_delay=60, job_name="unnamed"):
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"⏳ 작업 실행 중: {job_name} (시도 {attempt}/{max_retries})")
                await func()
                logger.info(f"✅ 작업 완료: {job_name}")
                return
            except Exception as e:
                logger.error(f"⚠️ 작업 실패 ({job_name}, {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    logger.info(f"⏳ {retry_delay}초 후 재시도 예정...")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"❌ 작업 최종 실패: {job_name} (모든 재시도 소진)")

    def add_daily_report(self, callback, hour=7, minute=0):
        self.scheduler.add_job(
            lambda: asyncio.create_task(self._retry_wrapper(callback, job_name="daily_report")),
            trigger=CronTrigger(hour=hour, minute=minute, timezone="Asia/Seoul"),
            id="daily_report",
            replace_existing=True
        )
        logger.info(f"📅 일일 리포트 스케줄 등록 (재시도 3회): 매일 {hour:02d}:{minute:02d} KST")

    def add_feedback_learning(self, callback, hour=17, minute=0):
        self.scheduler.add_job(
            lambda: asyncio.create_task(self._retry_wrapper(callback, job_name="feedback_learning")),
            trigger=CronTrigger(hour=hour, minute=minute, timezone="Asia/Seoul"),
            id="feedback_learning",
            replace_existing=True
        )
        logger.info(f"🧠 피드백 학습 스케줄 등록 (재시도 3회): 매일 {hour:02d}:{minute:02d} KST")

    def start(self):
        self.scheduler.start()
        logger.info("⏰ Scheduler started")

    def shutdown(self):
        self.scheduler.shutdown(wait=False)
        logger.info("⏰ Scheduler shutdown")