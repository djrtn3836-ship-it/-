"""
scheduler/macro_collector.py - v2.2 (순환 참조 제거)
- _send_alert()에서 scanner_main 동적 import 제거 → 콜백 패턴으로 변경
- set_alert_callback() 함수 추가
- 기존 데이터 수집 로직 100% 유지
"""

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import requests

from collector.collector_status import collector_status
from core.debug_tower import debug_tower
from core.logger import setup_logger

logger = setup_logger("macro_collector")

# ============================================================
# 콜백 패턴 (순환 참조 제거)
# ============================================================
_alert_callback: Callable[[str, str], None] | None = None


def set_alert_callback(func: Callable[[str, str], None]) -> None:
    """scanner_main에서 알림 함수를 등록"""
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
    """콜백을 통해 알림 전송 (순환 참조 제거)"""
    global _LAST_ALERT_TIME
    now = time.time()
    if now - _LAST_ALERT_TIME < _ALERT_COOLDOWN:
        return
    _LAST_ALERT_TIME = now
    if _alert_callback:
        await _alert_callback("📊 거시 데이터 수집 위기", error_msg)


def _is_anomaly(value: float, mean: float, std: float, z_threshold: float = 3.0) -> bool:
    if std == 0:
        return False
    return abs((value - mean) / std) > z_threshold


async def fetch_macro_data(force: bool = False) -> MacroData:
    global _cached_macro, _last_fetch_time, _consecutive_failures

    collector_status.register("macro_collector", freshness_seconds=600)

    now = time.time()
    if not force and _cached_macro and (now - _last_fetch_time < 600):
        logger.debug("📊 거시 데이터 캐시 사용 (10분 이내)")
        return _cached_macro

    logger.info("📊 글로벌 거시 데이터 수집 시작...")
    debug_tower.log("SYSTEM", "MACRO_FETCH_START", {})

    data = _cached_macro if _cached_macro else MacroData()
    _ = False
    loop = asyncio.get_running_loop()

    try:
        kospi = await loop.run_in_executor(None, _fetch_yahoo, "^KS200", "5d")
        if kospi is not None:
            data.kospi_trend = kospi
            logger.info(f"   ✅ KOSPI: {kospi:.2f}%")
        else:
            logger.warning("   ⚠️ KOSPI 수집 실패, 이전값 유지")

        usd = await loop.run_in_executor(None, _fetch_yahoo, "KRW=X", "1d")
        if usd and usd > 0:
            data.usdkrw = usd
            logger.info(f"   ✅ USD/KRW: {usd:.2f}")

        vix = await loop.run_in_executor(None, _fetch_yahoo, "^VIX", "1d")
        if vix and vix > 0:
            data.vix = vix
            data.vkospi = vix * 0.8
            logger.info(f"   ✅ VIX: {vix:.2f}")
        else:
            vix_fallback = await loop.run_in_executor(None, _fetch_fred, "VIXCLS")
            if vix_fallback and vix_fallback > 0:
                data.vix = vix_fallback
                data.vkospi = vix_fallback * 0.8
                logger.info(f"   ✅ VIX (FRED Fallback): {vix_fallback:.2f}")

        bond = await loop.run_in_executor(None, _fetch_yahoo, "^TNX", "1d")
        if bond and bond > 0:
            data.bond_3y = bond
            logger.info(f"   ✅ US 10Y: {bond:.2f}%")
        else:
            bond_fallback = await loop.run_in_executor(None, _fetch_fred, "DGS10")
            if bond_fallback and bond_fallback > 0:
                data.bond_3y = bond_fallback
                logger.info(f"   ✅ US 10Y (FRED Fallback): {bond_fallback:.2f}%")

        spx = await loop.run_in_executor(None, _fetch_yahoo, "^GSPC", "5d")
        if spx is not None:
            data.spx_trend = spx
            logger.info(f"   ✅ S&P 500: {spx:.2f}%")

        ndx = await loop.run_in_executor(None, _fetch_yahoo, "^NDX", "5d")
        if ndx is not None:
            data.ndx_trend = ndx
            logger.info(f"   ✅ 나스닥: {ndx:.2f}%")

        sox = await loop.run_in_executor(None, _fetch_yahoo, "^SOX", "5d")
        if sox is not None:
            data.sox_trend = sox
            logger.info(f"   ✅ SOX: {sox:.2f}%")

        oil = await loop.run_in_executor(None, _fetch_yahoo, "CL=F", "1d")
        if oil and oil > 0:
            data.oil_price = oil
            logger.info(f"   ✅ WTI: ${oil:.2f}")

        ktb = await loop.run_in_executor(None, _fetch_ktb_yield)
        if ktb and ktb > 0:
            data.ktb_3y = ktb
            logger.info(f"   ✅ KTB 3Y: {ktb:.2f}%")

        if data.vix < 0 or data.vix > 100:
            logger.warning(f"⚠️ VIX 이상치 감지: {data.vix:.2f} → 20.0으로 대체")
            data.vix = 20.0
            data.vkospi = 16.0

        data.last_update = datetime.now().isoformat()
        _cached_macro = data
        _last_fetch_time = time.time()
        _consecutive_failures = 0
        _ = True

        collector_status.record_success("macro_collector", data.to_dict())
        logger.info("📊 글로벌 거시 데이터 수집 완료")
        debug_tower.log("SYSTEM", "MACRO_FETCH_SUCCESS", data.to_dict())

    except Exception as e:
        _consecutive_failures += 1
        collector_status.record_failure("macro_collector", str(e))
        logger.error(f"❌ 거시 수집 실패 ({_consecutive_failures}회): {e}")
        debug_tower.capture_snapshot("SYSTEM", e, "MACRO_FETCH")
        if _consecutive_failures >= 3:
            await _send_alert(f"{_consecutive_failures}회 연속 실패: {e!s}")

    return _cached_macro if _cached_macro else MacroData()


def get_cached_macro() -> MacroData:
    return _cached_macro if _cached_macro else MacroData()


async def refresh_macro_if_needed(force: bool = False) -> MacroData:
    return await fetch_macro_data(force=force)
