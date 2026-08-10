"""
Timing Manager v5.1.2
판단 타이밍 관리 (하루 4회 + 이벤트 기반)
"""

import asyncio
from datetime import datetime, time
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
        
        # 다음 날 08:50까지 대기
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