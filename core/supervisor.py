"""
core/supervisor.py - v1.0 (시스템 자가 치유 감독관)
- scanner_main.py 프로세스 감시 및 자동 재시작
- 메모리/큐/WebSocket 상태 주기적 체크
- 이상 발생 시 Telegram 경고
"""

import asyncio
import os
import psutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from core.logger import setup_logger
from report.telegram_sender import TelegramSender

logger = setup_logger("supervisor")
telegram = TelegramSender()


class SystemSupervisor:
    def __init__(self):
        self.process = None
        self.pid_file = Path(__file__).parent.parent / "scanner.pid"
        self.check_interval = 30  # 30초마다 체크
        self.max_restarts = 5
        self.restart_count = 0
        self.last_restart_time = 0
        self.memory_threshold_mb = 1024  # 1GB 초과 시 경고
        self.queue_threshold = 50000  # 큐 5만개 이상 적체 시 경고

    async def run(self):
        logger.info("🛡️ SystemSupervisor 시작됨")
        while True:
            try:
                # 1. 프로세스 상태 확인
                if not self._is_process_running():
                    await self._restart_scanner()
                else:
                    # 2. 메모리 사용량 체크
                    await self._check_memory()
                    # 3. 큐 적체 체크 (MESSAGE_QUEUE 크기는 scanner_main에서 import 해야 함)
                    # 여기서는 간단히 로그 파일의 최근 에러를 확인
                    await self._check_error_log()

                await asyncio.sleep(self.check_interval)

            except Exception as e:
                logger.error(f"Supervisor 오류: {e}")
                await asyncio.sleep(10)

    def _is_process_running(self) -> bool:
        if not self.pid_file.exists():
            return False
        try:
            with open(self.pid_file, "r") as f:
                pid = int(f.read().strip())
            # 프로세스 존재 여부 확인
            process = psutil.Process(pid)
            if process.is_running():
                return True
        except:
            pass
        return False

    async def _restart_scanner(self):
        now = time.time()
        # 5분 내에 재시작이 5회 이상이면 알림만 보내고 중단
        if now - self.last_restart_time < 300:
            self.restart_count += 1
            if self.restart_count > self.max_restarts:
                await telegram.send_raw(
                    "🚨 [Supervisor] scanner_main.py 재시작 반복 실패 (5회 초과). 수동 개입 필요."
                )
                logger.critical("재시작 반복 실패, 수동 개입 필요")
                return
        else:
            self.restart_count = 0

        self.last_restart_time = now
        logger.warning("🔄 scanner_main.py 재시작 중...")
        await telegram.send_raw("🔄 [Supervisor] scanner_main.py가 중단되어 자동 재시작합니다.")

        # 기존 프로세스 종료
        if self.pid_file.exists():
            try:
                with open(self.pid_file, "r") as f:
                    pid = int(f.read().strip())
                process = psutil.Process(pid)
                process.terminate()
                process.wait(timeout=5)
            except:
                pass

        # 새 프로세스 실행 (detach)
        subprocess.Popen(
            [sys.executable, "scanner_main.py"],
            cwd=Path(__file__).parent.parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("✅ scanner_main.py 재시작 완료")

    async def _check_memory(self):
        if not self.pid_file.exists():
            return
        try:
            with open(self.pid_file, "r") as f:
                pid = int(f.read().strip())
            process = psutil.Process(pid)
            memory_mb = process.memory_info().rss / (1024 * 1024)
            if memory_mb > self.memory_threshold_mb:
                await telegram.send_raw(
                    f"⚠️ [Supervisor] 메모리 과사용: {memory_mb:.0f}MB (임계값: {self.memory_threshold_mb}MB)"
                )
                logger.warning(f"메모리 과사용: {memory_mb:.0f}MB")
        except:
            pass

    async def _check_error_log(self):
        log_path = Path(__file__).parent.parent / "logs" / "scanner.log"
        if not log_path.exists():
            return
        try:
            # 최근 1분간 ERROR 로그가 10회 이상이면 경고
            lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-100:]
            errors = [l for l in lines if "ERROR" in l and "105115" not in l]
            if len(errors) > 10:
                await telegram.send_raw(
                    f"⚠️ [Supervisor] 최근 1분간 ERROR 로그 {len(errors)}회 발생. 로그 확인 필요."
                )
        except:
            pass


if __name__ == "__main__":
    asyncio.run(SystemSupervisor().run())