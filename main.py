#!/usr/bin/env python3
"""
v5.1.2 FINAL - 백그라운드 프로세스 (main.py)
Phase 1 Shadow Mode 가동용
"""

import asyncio
import logging
from pathlib import Path

# 프로젝트 루트 추가
import sys
sys.path.insert(0, str(Path(__file__).parent))

from core.logger import setup_logger
from orchestrator.pipeline_manager import PipelineManager
from monitor.daily_monitor import DailyMonitor
from monitor.shadow_logger import ShadowLogger

logger = setup_logger("main")

async def main():
    logger.info("=" * 60)
    logger.info("v5.1.2 FINAL - Phase 1 Shadow Mode 가동")
    logger.info("가동 일시: 2026-08-12 08:00 KST")
    logger.info("=" * 60)

    pipeline = None
    monitor_task = None
    shadow_task = None

    try:
        # 1. 파이프라인 매니저
        pipeline = PipelineManager()
        await pipeline.start()

        # 2. Daily Monitor
        monitor = DailyMonitor()
        monitor_task = asyncio.create_task(monitor.run())

        # 3. Shadow Logger
        shadow_logger = ShadowLogger()
        shadow_task = asyncio.create_task(shadow_logger.run())

        logger.info("✅ 모든 백그라운드 프로세스 시작됨")

        # 종료 대기 (Ctrl+C 또는 CancelledError)
        await asyncio.Event().wait()

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("⏹ 종료 신호 수신 (Ctrl+C)")

    finally:
        # 정리 작업
        logger.info("🔄 시스템 정리 중...")

        if monitor_task and not monitor_task.done():
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

        if shadow_task and not shadow_task.done():
            shadow_task.cancel()
            try:
                await shadow_task
            except asyncio.CancelledError:
                pass

        if pipeline:
            await pipeline.stop()

        logger.info("✅ 시스템 종료 완료")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("시스템 종료 (KeyboardInterrupt)")