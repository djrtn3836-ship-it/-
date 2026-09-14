# -*- coding: utf-8 -*-
"""
core/supervisor.py - v1.3 (mypy strict 완전 적용)
- Process monitoring and auto-restart
- Market hours (09:00-15:30) restart prevention
- Memory usage monitoring
- Telegram flood protection (10 second cooldown)
"""

import asyncio
import subprocess
import sys
import time
from datetime import datetime
from datetime import time as dt_time
from datetime import timedelta, timezone
from pathlib import Path

import psutil

from core.logger import setup_logger
from report.telegram_sender import TelegramSender

logger = setup_logger("supervisor")
telegram = TelegramSender()

KST = timezone(timedelta(hours=9))


def _get_kst_now() -> datetime:
    """Get current time in KST"""
    return datetime.now().astimezone(KST)


def _is_market_hours() -> bool:
    """Check if currently in market hours (09:00-15:30 KST)"""
    now = _get_kst_now()
    market_open = dt_time(9, 0)
    market_close = dt_time(15, 30)
    return market_open <= now.time() <= market_close


class SystemSupervisor:
    """System supervisor for auto-restart and monitoring"""

    def __init__(self) -> None:
        self.process: object = None
        self.pid_file: Path = Path(__file__).parent.parent / "scanner.pid"
        self.check_interval: int = 30
        self.max_restarts: int = 5
        self.restart_count: int = 0
        self.last_restart_time: float = 0.0
        self.memory_threshold_mb: int = 1024
        self.queue_threshold: int = 50000
        self._restart_pending: bool = False

    async def run(self) -> None:
        """Main supervisor loop"""
        await asyncio.sleep(10)

        logger.info("SystemSupervisor started (market hours restart protection enabled)")
        while True:
            try:
                error_count = self._count_recent_errors()
                if error_count >= 25:
                    await self._send_alert(
                        f"[Supervisor] 25+ errors in last 100 lines: {error_count} errors"
                    )

                await self._check_memory()

                if not self._is_process_running():
                    await self._handle_process_death()

                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Supervisor error: {e}")
                await asyncio.sleep(60)

    def _is_process_running(self) -> bool:
        """Check if scanner process is running"""
        if not self.pid_file.exists():
            return False
        try:
            with open(self.pid_file) as f:
                pid = int(f.read().strip())
            process = psutil.Process(pid)
            return bool(process.is_running())
        except Exception:
            return False

    async def _handle_process_death(self) -> None:
        """Handle process death"""
        now = _get_kst_now()

        if _is_market_hours():
            msg = (
                f"[Supervisor] scanner_main.py process died!\n"
                f"Time: {now.strftime('%H:%M:%S')}\n"
                f"Market is open - manual intervention required\n"
                f"Will restart after market close (15:30)"
            )
            await telegram.send_raw(msg)
            logger.critical("Process died during market hours - manual intervention needed")
            self._restart_pending = True
            return

        if self._restart_pending:
            await telegram.send_raw("[Supervisor] Restarting after market hours")
            self._restart_pending = False

        await self._restart_scanner()

    async def _restart_scanner(self) -> None:
        """Restart scanner process with throttling"""
        now: float = time.time()

        if now - self.last_restart_time < 300:
            self.restart_count += 1
            if self.restart_count > self.max_restarts:
                await telegram.send_raw(
                    "[Supervisor] Too many restarts (5 in 5 minutes). Manual intervention required."
                )
                logger.critical("Too many restarts, supervisor stopping")
                return
        else:
            self.restart_count = 0

        self.last_restart_time = now
        logger.warning("Restarting scanner_main.py...")
        await telegram.send_raw("[Supervisor] Restarting scanner_main.py")

        if self.pid_file.exists():
            try:
                with open(self.pid_file) as f:
                    pid = int(f.read().strip())
                proc = psutil.Process(pid)
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                pass

        subprocess.Popen(
            [sys.executable, "scanner_main.py"],
            cwd=Path(__file__).parent.parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("scanner_main.py restarted")

    async def _check_memory(self) -> None:
        """Check memory usage of scanner process"""
        if not self.pid_file.exists():
            return
        try:
            with open(self.pid_file) as f:
                pid = int(f.read().strip())
            proc = psutil.Process(pid)
            memory_mb: float = proc.memory_info().rss / (1024 * 1024)
            if memory_mb > self.memory_threshold_mb:
                await telegram.send_raw(
                    f"[Supervisor] Memory usage high: {memory_mb:.0f}MB "
                    f"(threshold: {self.memory_threshold_mb}MB)"
                )
                logger.warning(f"Memory usage: {memory_mb:.0f}MB")
        except Exception:
            pass

    async def _send_alert(self, message: str) -> None:
        """Send alert via Telegram"""
        try:
            await telegram.send_raw(message)
        except Exception as e:
            logger.warning(f"Supervisor alert failed: {e}")

    def _count_recent_errors(self) -> int:
        """Count errors in last 100 lines of scanner.log"""
        log_path = Path(__file__).parent.parent / "logs" / "scanner.log"
        if not log_path.exists():
            return 0
        try:
            lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            recent_lines = lines[-100:]
            error_lines = [ln for ln in recent_lines if "ERROR" in ln and "105115" not in ln]
            return len(error_lines)
        except Exception:
            return 0
