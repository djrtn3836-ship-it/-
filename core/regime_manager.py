"""
core/regime_manager.py - 중앙 국면 관리자 v1.1 (import 경로 수정)
- 백그라운드에서 주기적으로(60초) 시장 국면을 갱신
- 🔥 수정: RegimeDetector import 경로를 'regime.regime_detector'로 수정
- 다른 모듈은 RegimeManager.get_regime()으로 즉시 국면 조회 가능
"""

import asyncio
import time
import logging
from typing import Optional
from datetime import datetime

# 🔥 import 경로 수정 (regime 폴더에서 가져옴)
from regime.regime_detector import RegimeDetector
from scheduler.macro_collector import get_cached_macro

logger = logging.getLogger(__name__)


class RegimeManager:
    """싱글톤 국면 관리자"""
    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._detector = RegimeDetector()
        self._current_regime = "Sideways"
        self._last_update_time = 0.0
        self._update_interval = 60
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._update_loop())
        logger.info("✅ RegimeManager 시작됨 (갱신 간격: %d초)", self._update_interval)

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 RegimeManager 중지됨")

    async def _update_loop(self):
        await self._update_regime()
        while self._running:
            await asyncio.sleep(self._update_interval)
            await self._update_regime()

    async def _update_regime(self):
        """실제 국면 계산 및 캐시 갱신 (거시 데이터 포함)"""
        try:
            macro = get_cached_macro()
            data = {
                "kospi_trend": macro.kospi_trend,
                "vix": macro.vix,
                "vkospi": macro.vkospi,
                "usdkrw_change_pct": 0.0,
                "foreigner_net": macro.foreigner_futures,
                "institution_net": 0.0,
                "program_buy": 0.0,
                "program_sell": 0.0,
                "date": datetime.now(),
            }

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self._detector.detect, data)

            new_regime = result.get('regime', 'Sideways')
            if new_regime != self._current_regime:
                logger.info(f"🔄 시장 국면 변경: {self._current_regime} → {new_regime}")
            self._current_regime = new_regime
            self._last_update_time = time.time()

        except Exception as e:
            logger.warning(f"⚠️ 국면 갱신 실패: {e}, 현재값 유지: {self._current_regime}")

    def get_regime(self) -> str:
        return self._current_regime

    def get_last_update_time(self) -> float:
        return self._last_update_time

    def get_status(self) -> dict:
        return {
            "current_regime": self._current_regime,
            "last_update_ago": time.time() - self._last_update_time if self._last_update_time else 0,
            "is_running": self._running,
        }


# 전역 인스턴스
regime_manager = RegimeManager()