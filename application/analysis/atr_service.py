# application/analysis/atr_service.py - v1.1 (mypy strict 적용 - Session 24)
from typing import Any, Optional, Callable, List, Dict

import numpy as np
from cachetools import TTLCache

from config.schema import get_config
from core.logger import setup_logger
from data.db_manager import DatabaseManager
from observability.auto_trace import TracedService
from observability.tracer import get_tracer

logger = setup_logger("atr_service")
config = get_config()
trace = get_tracer(__name__)


class AtrService(TracedService):
    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        realtime_price_provider: Optional[Callable[[str], float]] = None,
    ) -> None:
        self.db: Optional[DatabaseManager] = db_manager
        self._realtime_price_provider: Optional[Callable[[str], float]] = realtime_price_provider
        self._cache: TTLCache = TTLCache(maxsize=500, ttl=120)
        self._fallback_ratio: float = config.trading.atr_fallback_ratio
        trace.debug("ATR service initialized", fallback_ratio=self._fallback_ratio)

    def set_realtime_price_provider(self, provider: Callable[[str], float]) -> None:
        self._realtime_price_provider = provider
        trace.debug("Realtime price provider set")

    async def calculate(self, ticker: str, period: int = 14) -> float:
        cache_key: str = f"{ticker}:{period}"
        if cache_key in self._cache:
            cached_value: float = self._cache[cache_key]
            if cached_value > 0:
                trace.debug("ATR cache hit", ticker=ticker, value=cached_value)
                return cached_value

        trace.debug("ATR calculation started", ticker=ticker, period=period)
        atr: float = await self._calculate_from_db(ticker, period)
        if atr > 0:
            self._cache[cache_key] = atr
            return atr

        hv: float = await self._calculate_historical_volatility(ticker, 20)
        if hv > 0:
            fallback: float = hv * 0.1
            self._cache[cache_key] = fallback
            trace.info("ATR Fallback (HV)", ticker=ticker, fallback=fallback)
            return fallback

        latest_price: float = await self._get_latest_price(ticker)
        if latest_price > 0:
            fallback = latest_price * self._fallback_ratio
            self._cache[cache_key] = fallback
            trace.warning("ATR Final Fallback (price)", ticker=ticker, fallback=fallback, price=latest_price)
            return fallback

        trace.error("ATR calculation completely failed", ticker=ticker)
        self._cache[cache_key] = 0.0
        return 0.0

    async def _calculate_from_db(self, ticker: str, period: int) -> float:
        if not self.db:
            return 0.0
        try:
            ohlcv_list: List[Dict[str, Any]] = await self.db.get_ohlcv(ticker, period)
            clean_list: List[Dict[str, Any]] = [d for d in ohlcv_list if d.get("high", 0) > 0 and d.get("low", 0) > 0]
            if len(clean_list) < 2:
                return 0.0
            tr_values: List[float] = []
            for i in range(1, len(clean_list)):
                high: float = float(clean_list[i]["high"])
                low: float = float(clean_list[i]["low"])
                prev_close: float = float(clean_list[i - 1]["close"])
                tr_values.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
            return round(sum(tr_values) / len(tr_values), 2) if tr_values else 0.0
        except Exception as e:
            logger.debug(f"DB ATR failed ({ticker}): {e}")
            return 0.0

    async def _calculate_historical_volatility(self, ticker: str, window: int = 20) -> float:
        if not self.db:
            return 0.0
        try:
            data: List[Dict[str, Any]] = await self.db.get_ohlcv(ticker, window + 1)
            if len(data) < 2:
                return 0.0
            returns: List[float] = []
            for i in range(1, len(data)):
                prev: float = float(data[i - 1]["close"])
                curr: float = float(data[i]["close"])
                if prev > 0:
                    returns.append((curr - prev) / prev)
            return float(np.std(returns)) if len(returns) >= 2 else 0.0
        except Exception as e:
            logger.debug(f"HV failed ({ticker}): {e}")
            return 0.0

    async def _get_latest_price(self, ticker: str) -> float:
        if self.db:
            try:
                ohlcv: List[Dict[str, Any]] = await self.db.get_ohlcv(ticker, 1)
                if ohlcv:
                    return float(ohlcv[-1]["close"])
            except Exception:
                pass
        if self._realtime_price_provider:
            try:
                price: float = self._realtime_price_provider(ticker)
                if price and price > 0:
                    trace.debug("Realtime price fallback", ticker=ticker, price=price)
                    return price
            except Exception as e:
                logger.debug(f"Realtime price fallback failed ({ticker}): {e}")
        return 0.0

    def clear_cache(self, ticker: Optional[str] = None) -> None:
        if ticker:
            for k in [k for k in self._cache if k.startswith(ticker)]:
                del self._cache[k]
        else:
            self._cache.clear()
