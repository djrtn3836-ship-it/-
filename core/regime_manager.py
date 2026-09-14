"""
core/regime_manager.py - v1.3 (mypy 오류 전면 해결 + Whipsaw 방지)
- 반환 타입 전면 명시, _initialized 클래스 레벨 선언 (core/config.py 패턴과 일치)
- 원시 신호가 연속 2회 동일하게 감지될 때만 공식 전환 (SNOWBALL 확정 카운터 패턴)
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

from regime.regime_detector import RegimeDetector
from scheduler.macro_collector import get_cached_macro

logger = logging.getLogger(__name__)


class RegimeManager:
    """싱글톤 국면 관리자"""

    _instance: Optional["RegimeManager"] = None
    _lock = asyncio.Lock()
    _initialized: bool

    def __new__(cls) -> "RegimeManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        self._detector = RegimeDetector()
        self._current_regime: str = "Sideways"
        self._raw_regime_candidate: str = "Sideways"
        self._confirm_count: int = 0
        self._CONFIRM_THRESHOLD: int = 2

        self._last_update_time: float = 0.0
        self._update_interval: int = 60
        self._task: Optional["asyncio.Task[None]"] = None
        self._running: bool = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._update_loop())
        logger.info(
            "RegimeManager 시작됨 (갱신 간격: %d초, 확정 기준: %d회)",
            self._update_interval, self._CONFIRM_THRESHOLD,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("RegimeManager 중지됨")

    async def _update_loop(self) -> None:
        await self._update_regime()
        while self._running:
            await asyncio.sleep(self._update_interval)
            await self._update_regime()

    async def _update_regime(self) -> None:
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

            raw_regime: str = str(result.get("regime", "Sideways"))

            if raw_regime == self._raw_regime_candidate:
                self._confirm_count += 1
            else:
                self._raw_regime_candidate = raw_regime
                self._confirm_count = 1

            if self._confirm_count >= self._CONFIRM_THRESHOLD:
                if raw_regime != self._current_regime:
                    logger.info(
                        "시장 국면 확정 전환: %s -> %s (%d회 연속 확인)",
                        self._current_regime, raw_regime, self._confirm_count,
                    )
                self._current_regime = raw_regime
                self._confirm_count = 0

            self._last_update_time = time.time()

        except Exception as e:
            logger.warning("국면 갱신 실패: %s, 현재값 유지: %s", e, self._current_regime)

    def get_regime(self) -> str:
        return self._current_regime

    def get_last_update_time(self) -> float:
        return self._last_update_time

    def get_status(self) -> Dict[str, Any]:
        return {
            "current_regime": self._current_regime,
            "candidate_regime": self._raw_regime_candidate,
            "confirm_count": self._confirm_count,
            "last_update_ago": time.time() - self._last_update_time if self._last_update_time else 0,
            "is_running": self._running,
        }


regime_manager: RegimeManager = RegimeManager()