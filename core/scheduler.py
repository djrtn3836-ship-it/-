"""
core/scheduler.py - v2.1 — Claude 버그 수정 (시스템 시작 크래시)

수정 사항 (v2.0 → v2.1):
- 🔥 CRITICAL: add_job_with_retry(self, coro_func, trigger, job_id,
  max_retries=3, retry_delay=5, *args, **kwargs) 시그니처에서
  max_retries/retry_delay가 *args보다 앞에 있어, scanner_main.py처럼
  "coro_func의 실제 인자(daily_reporter, analyzer 등)를 위치인자로 넘기고
  max_retries/retry_delay는 키워드로 넘기는" 호출 패턴과 충돌.
  4번째 위치인자(예: daily_reporter)가 max_retries 슬롯을 채워버려
  "got multiple values for argument 'max_retries'"로 시스템 시작 자체가
  크래시하던 버그. scanner_main.py에 등록된 4개 스케줄 작업
  (daily_report, feedback_learning, weekly_pdf, daily_ohlcv) 전부에서
  100% 재현되는 구조적 문제였음.
  → max_retries/retry_delay를 *args 뒤 키워드 전용 인자로 이동.
"""

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from core.blackbox_logger import log_error
from core.logger import setup_logger

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

    def add_job_with_retry(
        self, coro_func, trigger, job_id, *args, max_retries: int = 3, retry_delay: int = 5, **kwargs
    ):
        """
        재시도 로직이 포함된 스케줄 작업 등록

        🔥 수정됨: max_retries, retry_delay는 이제 키워드 전용(keyword-only)
        인자입니다. coro_func에 전달할 실제 인자는 job_id 뒤에 위치인자로
        자유롭게 넘기세요. 예:
            add_job_with_retry(run_daily_report, trigger, "daily_report",
                                daily_reporter, max_retries=3, retry_delay=5)
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
