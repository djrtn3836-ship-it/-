"""
Timing Manager v5.1.3 — Claude 버그 수정
판단 타이밍 관리 (하루 4회 + 이벤트 기반)

수정 사항 (v5.1.2 → v5.1.3):
- 🔥 CRITICAL: `timedelta`가 import되지 않아 매일 after_hours(16:30) 이후
  다음날 대기 로직 진입 시 NameError로 크래시하던 버그 수정
"""

import asyncio
from datetime import datetime, time, timedelta  # 🔥 timedelta 추가
from typing import Dict, Optional

from core.constants import MARKET_OPEN, MARKET_CLOSE
from core.logger import setup_logger

logger = setup_logger("timing")


class TimingManager:
    """판단 타이밍 관리자"""

    def __init__(self):
        self.times = {
            "pre_market": "08:50",
            "midday": "12:30",
            "pre_close": "15:20",
            "after_hours": "16:30"
        }
        self.last_decision: Optional[datetime] = None

    async def wait_for_next(self) -> str:
        """다음 판단 시간까지 대기"""
        now = datetime.now()

        for name, time_str in self.times.items():
            target = datetime.strptime(time_str, "%H:%M")
            target = target.replace(
                year=now.year, month=now.month, day=now.day
            )

            if target > now:
                wait_seconds = (target - now).total_seconds()
                await asyncio.sleep(wait_seconds)
                self.last_decision = datetime.now()
                return name

        # 다음 날 08:50까지 대기 (🔥 timedelta 정상 참조)
        tomorrow = now + timedelta(days=1)
        target = tomorrow.replace(hour=8, minute=50, second=0, microsecond=0)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        self.last_decision = datetime.now()
        return "pre_market"

    def is_trading_hours(self) -> bool:
        """정규장 여부"""
        now = datetime.now().time()
        open_time = time(9, 0)
        close_time = time(15, 30)
        return open_time <= now <= close_time
