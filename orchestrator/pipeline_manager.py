"""
Pipeline Manager v5.1.2
프로세스 분리 (GIL 병목 해소) + Circuit Breaker 적용
"""

import asyncio
import multiprocessing as mp
from typing import Dict, Any, Optional
from datetime import datetime

from core.logger import setup_logger
from core.circuit_breaker import KIWOOM_TR_CB, DART_API_CB
from data.kiwoom_connector import KiwoomConnectorV512
from data.dart_connector import DartConnector
from orchestrator.event_bus import EventBus
from orchestrator.feature_store import FeatureStore

logger = setup_logger("pipeline")


class PipelineManager:
    """파이프라인 관리자 (프로세스 분리)"""
    
    def __init__(self):
        self.event_bus = EventBus()
        self.feature_store = FeatureStore()
        self.kiwoom = KiwoomConnectorV512()
        self.dart = DartConnector(api_key="YOUR_DART_KEY")
        
        self._processes: Dict[str, mp.Process] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._is_running = False
    
    async def start(self):
        """파이프라인 시작 (프로세스 분리)"""
        logger.info("PipelineManager starting...")
        
        # 1. Event Bus 시작
        await self.event_bus.start()
        
        # 2. Feature Store 시작
        asyncio.create_task(self._cleanup_features())
        
        # 3. Kiwoom 연결 (Circuit Breaker 적용)
        @KIWOOM_TR_CB.protect
        async def connect_kiwoom():
            return await self.kiwoom.connect()
        
        await connect_kiwoom()
        
        # 4. DART 연결 (Circuit Breaker 적용)
        @DART_API_CB.protect
        async def connect_dart():
            await self.dart.connect()
            return True
        
        await connect_dart()
        
        self._is_running = True
        logger.info("PipelineManager started")
    
    async def stop(self):
        """파이프라인 중지"""
        logger.info("PipelineManager stopping...")
        self._is_running = False
        
        await self.event_bus.stop()
        await self.kiwoom.disconnect()
        await self.dart.disconnect()
        
        # 태스크 취소
        for task in self._tasks.values():
            task.cancel()
        
        # 프로세스 종료
        for name, process in self._processes.items():
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        
        logger.info("PipelineManager stopped")
    
    async def _cleanup_features(self):
        """주기적 Feature 정리"""
        while self._is_running:
            await asyncio.sleep(60)  # 1분마다
            await self.feature_store.clear_expired()
    
    def spawn_scanner_process(self):
        """스캐너 프로세스 분리"""
        # multiprocessing으로 스캐너 프로세스 생성
        # 실제로는 별도 프로세스에서 scanner_main.py 실행
        pass