# -*- coding: utf-8 -*-
"""
Daily Monitor v5.2.0
일일 모니터링 대시보드

변경 이력:
    v5.2.0  - @trace.traced 적용: run / _generate_report
            - CRLF → LF 변환 완료
    v5.1.2  - 초기 버전
"""

import asyncio
from datetime import datetime

from core.circuit_breaker import DART_API_CB, KIWOOM_TR_CB
from core.logger import setup_logger
from observability.tracer import get_tracer
from orchestrator.feature_store import FeatureStore

logger = setup_logger("monitor")
trace = get_tracer(__name__)


class DailyMonitor:
    """일일 모니터링 대시보드"""

    def __init__(self):
        self.feature_store = FeatureStore()
        self.report: dict = {}

    @trace.traced
    async def run(self):
        """모니터링 실행 (1시간 주기)"""
        logger.info("DailyMonitor started")

        while True:
            await asyncio.sleep(3600)  # 1시간마다
            self.report = await self._generate_report()
            logger.info(f"DailyMonitor report: {self.report}")

    @trace.traced
    async def _generate_report(self) -> dict:
        """모니터링 리포트 생성"""
        # 1. Feature Freshness
        stats = await self.feature_store.get_stats()

        # 2. Circuit Breaker 상태
        cb_status = {"kiwoom_tr": KIWOOM_TR_CB.get_stats(), "dart_api": DART_API_CB.get_stats()}

        # 3. 시스템 상태
        report = {
            "timestamp": datetime.now().isoformat(),
            "feature_freshness": {
                "fresh_ratio": stats.get("fresh_rate", 0),
                "status": "PASS" if stats.get("fresh_rate", 0) > 0.95 else "WARN",
            },
            "circuit_breaker": cb_status,
            "overall_status": "HEALTHY",
        }

        return report
