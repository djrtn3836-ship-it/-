"""
scheduler/macro_collector.py - v2.5 FINAL (이상치 강제 처리)
- KOSPI 변동률 ±30% 초과 시 0.0 강제 설정
- 장 마감 후 수집 스킵 (캐시 유지)
- USD/KRW, VIX 등 유효 범위 검사
"""

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timezone, timedelta
from typing import Callable

import requests

from collector.collector_status import collector_status
from core.debug_tower import debug_tower
from core.logger import setup_logger

logger = setup_logger("macro_collector")

_alert_callback: Callable[[str, str], None] | None = None


def set_alert_callback(func: Callable[[str, str], None]) -> None:
    global _alert_callback
    _alert_callback = func


@dataclass
class MacroData:
    kospi_trend: float = 0.0
    usdkrw: float = 1300.0
    bond_3y: float = 3.5
    vix: float = 20.0
    vkospi: float = 20.0
    foreigner_futures: float = 0.0
    spx_trend: float = 0.0
    ndx_trend: float = 0.0
    sox_trend: float = 0.0
    oil_price: float = 75.0
    ktb_3y: float = 3.0
    last_update: str = ""

    def to_dict(self) -> dict:
        return {
            "kospi_trend": self.kospi_trend,
            "usdkrw": self.usdkrw,
            "bond_3y": self.bond_3y,
            "vix": self.vix,
            "vkospi": self.vkospi,
            "foreigner_futures": self.foreigner_futures,
            "spx_trend": self.spx_trend,
            "ndx_trend": self.ndx_trend,
            "sox_trend": self.sox_trend,
            "oil_price": self.oil_price,
            "ktb_3y": self.ktb_3y,
            "last_update": self.last_update,
        }


_cached_macro: MacroData | None = None
_last_fetch_time: float = 0
_consecutive_failures: int = 0
_LAST_ALERT_TIME: float = 0
_ALERT_COOLDOWN = 1800

KST = timezone(timedelta(hours=9))


def _get_kst_now() -> datetime:
    return datetime.now().astimezone(KST)


def _is_market_hours() -> bool:
    now = _get_kst_now()
    market_open = dt_time(9, 0)
    market_close = dt_time(15, 30)
    return market_open <= now.time() <= market_close


def _is_valid_value(value: float, min_val: float, max_val: float) -> bool:
    return min_val <= value <= max_val


def _fetch_yahoo(symbol: str, period: str = "5d") -> float | None:
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period)
        if hist.empty:
            return None
        if period == "5d" and len(hist) >= 2:
            old = hist["Close"].iloc[0]
            latest = hist["Close"].iloc[-1]
            return (latest - old) / old * 100
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.debug(f"Yahoo Finance 오류 ({symbol}): {e}")
        return None


def _fetch_fred(series_id: str) -> float | None:
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            lines = resp.text.strip().split("\n")
            if len(lines) >= 2:
                last_line = lines[-1].split(",")
                if len(last_line) >= 2:
                    return float(last_line[1])
    except Exception as e:
        logger.debug(f"FRED API 오류 ({series_id}): {e}")
    return None


def _fetch_ktb_yield() -> float | None:
    try:
        url = "https://finance.naver.com/marketindex/interestDailyQuote.nhn?marketindexCd=IRR_KTB3Y"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        match = re.search(r'<td class="num">([\d.]+)</td>', resp.text)
        if match:
            return float(match.group(1))
    except Exception as e:
        logger.debug(f"KTB 수집 실패: {e}")
    return None


async def _send_alert(error_msg: str) -> None:
    global _LAST_ALERT_TIME
    now = time.time()
    if now - _LAST_ALERT_TIME < _ALERT_COOLDOWN:
        return
    _LAST_ALERT_TIME = now
    if _alert_callback:
        await _alert_callback("📊 거시 데이터 수집 위기", error_msg)


async def fetch_macro_data(force: bool = False) -> MacroData:
    global _cached_macro, _last_fetch_time, _consecutive_failures

    collector_status.register("macro_collector", freshness_seconds=600)

    now = time.time()
    if not force and _cached_macro and (now - _last_fetch_time < 600):
        logger.debug("📊 거시 데이터 캐시 사용 (10분 이내)")
        return _cached_macro

    if not _is_market_hours() and not force and _cached_macro is not None:
        logger.debug("📊 장 마감 후 → 거시 데이터 수집 스킵 (캐시 사용)")
        return _cached_macro

    logger.info("📊 글로벌 거시 데이터 수집 시작...")
    debug_tower.log("SYSTEM", "MACRO_FETCH_START", {})

    data = _cached_macro if _cached_macro else MacroData()
    loop = asyncio.get_running_loop()

    # (field, symbol, period, default, min, max)
    item_configs = [
        ("kospi_trend", "^KS200", "5d", 0.0, -30.0, 30.0),
        ("usdkrw", "KRW=X", "1d", 1300.0, 1100.0, 1550.0),
        ("vix", "^VIX", "1d", 20.0, 5.0, 80.0),
        ("bond_3y", "^TNX", "1d", 3.5, 0.0, 8.0),
        ("spx_trend", "^GSPC", "5d", 0.0, -30.0, 30.0),
        ("ndx_trend", "^NDX", "5d", 0.0, -30.0, 30.0),
        ("sox_trend", "^SOX", "5d", 0.0, -30.0, 30.0),
        ("oil_price", "CL=F", "1d", 75.0, 20.0, 150.0),
    ]

    for field, symbol, period, default, min_val, max_val in item_configs:
        try:
            value = await loop.run_in_executor(None, _fetch_yahoo, symbol, period)
            old_value = getattr(_cached_macro, field, default) if _cached_macro else default

            if value is None:
                logger.warning(f"   ⚠️ {field} 수집 실패, 캐시값 {old_value:.2f} 유지")
                setattr(data, field, old_value)
            elif _is_valid_value(value, min_val, max_val):
                setattr(data, field, value)
                logger.info(f"   ✅ {field}: {value:.2f}")
            else:
                # 🔥 이상치: 캐시값 유지 (0으로 대체하지 않음)
                logger.warning(f"   ⚠️ {field} 이상치 ({value:.2f}) → 캐시값 {old_value:.2f} 유지")
                setattr(data, field, old_value)
        except Exception as e:
            logger.warning(f"   ⚠️ {field} 처리 오류: {e}")

    # KTB
    try:
        ktb = await loop.run_in_executor(None, _fetch_ktb_yield)
        old_ktb = _cached_macro.ktb_3y if _cached_macro else 3.0
        if ktb is not None and 0 < ktb < 6:
            data.ktb_3y = ktb
            logger.info(f"   ✅ KTB 3Y: {ktb:.2f}%")
        else:
            logger.warning(f"   ⚠️ KTB 이상치 ({ktb}) → 캐시값 {old_ktb:.2f} 유지")
            data.ktb_3y = old_ktb
    except Exception as e:
        logger.warning(f"   ⚠️ KTB 수집 오류: {e}")

    # KOSPI 이상치 강제 보정 (추가 안전장치)
    if abs(data.kospi_trend) > 30:
        logger.warning(f"🔥 KOSPI 이상치 강제 보정: {data.kospi_trend:.2f}% → 0.0")
        data.kospi_trend = 0.0

    data.vkospi = data.vix * 0.8
    data.last_update = _get_kst_now().isoformat()

    _cached_macro = data
    _last_fetch_time = time.time()
    _consecutive_failures = 0

    collector_status.record_success("macro_collector", data.to_dict())
    logger.info("📊 글로벌 거시 데이터 수집 완료")
    debug_tower.log("SYSTEM", "MACRO_FETCH_SUCCESS", data.to_dict())
    return data


def get_cached_macro() -> MacroData:
    return _cached_macro if _cached_macro else MacroData()


async def refresh_macro_if_needed(force: bool = False) -> MacroData:
    return await fetch_macro_data(force=force)