"""
Shadow Logger v5.1.2
Shadow Mode 로거 (의사결정 전체 기록)
"""

import asyncio
import csv
from datetime import datetime
from pathlib import Path

from core.logger import setup_logger

logger = setup_logger("shadow")


class ShadowLogger:
    """Shadow Mode 로거"""

    def __init__(self, log_dir: str = "./logs/shadow"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False

    async def run(self):
        """로거 실행"""
        self._running = True
        logger.info("ShadowLogger started")

        while self._running:
            try:
                # 1분마다 로그 저장
                await asyncio.sleep(60)
                await self._flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"ShadowLogger error: {e}")

    async def log(self, data: dict):
        """의사결정 로그 저장"""
        await self._queue.put(data)

    async def _flush(self):
        """큐에 쌓인 로그 저장"""
        if self._queue.empty():
            return

        filename = self.log_dir / f"shadow_{datetime.now().strftime('%Y%m%d')}.csv"

        # CSV 저장
        with open(filename, "a", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "timestamp",
                    "ticker",
                    "action",
                    "score",
                    "confidence",
                    "regime",
                    "price",
                    "volume",
                    "positive_factors",
                    "negative_factors",
                ],
            )

            if f.tell() == 0:
                writer.writeheader()

            while not self._queue.empty():
                try:
                    data = self._queue.get_nowait()
                    writer.writerow(data)
                except asyncio.QueueEmpty:
                    break
