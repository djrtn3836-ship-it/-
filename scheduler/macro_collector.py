"""
scheduler/macro_collector.py - v2.0 (글로벌 거시 확장 + KTB 수집)
- 기존 KOSPI/USDKRW/VIX/금리에 S&P 500, 나스닥, SOX, WTI, KTB 3년물 추가
- Yahoo Finance + Naver Finance 하이브리드 수집
- 10분 TTL 캐싱, 3회 연속 실패 시 Telegram 경고
"""

import asyncio
import time
import re
import requests
from datetime import datetime
from typing import Optional, Dict
from dataclasses import dataclass, field

from core.logger import setup_logger
from core.debug_tower import debug_tower

logger = setup_logger("macro_collector")

# ============================================================
# 데이터 클래스 (확장)
# ============================================================
@dataclass
class MacroData:
    # 기존
    kospi_trend: float = 0.0
    usdkrw: float = 1300.0
    bond_3y: float = 3.5
    vix: float = 20.0
    vkospi: float = 20.0
    foreigner_futures: float = 0.0
    
    # 🔥 신규 (글로벌)
    spx_trend: float = 0.0        # S&P 500 5일 수익률
    ndx_trend: float = 0.0        # 나스닥 100 5일 수익률
    sox_trend: float = 0.0        # 필라델피아 반도체 5일 수익률
    oil_price: float = 75.0       # WTI 원유 가격
    ktb_3y: float = 3.0           # 한국 국채 3년물 금리 (%)
    
    last_update: str = ""

    def to_dict(self) -> Dict:
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


# ============================================================
# 전역 변수
# ============================================================
_cached_macro: Optional[MacroData] = None
_last_fetch_time: float = 0
_consecutive_failures: int = 0
_LAST_ALERT_TIME: float = 0
_ALERT_COOLDOWN = 1800


# ============================================================
# 내부 수집 함수 (동기)
# ============================================================
def _fetch_yahoo(symbol: str, period: str = "5d") -> Optional[float]:
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period)
        if hist.empty:
            return None
        if period == "5d" and len(hist) >= 2:
            old = hist['Close'].iloc[0]
            latest = hist['Close'].iloc[-1]
            return (latest - old) / old * 100
        return float(hist['Close'].iloc[-1])
    except Exception as e:
        logger.debug(f"Yahoo Finance 오류 ({symbol}): {e}")
        return None


def _fetch_ktb_yield() -> Optional[float]:
    """Naver Finance에서 KTB 3년물 금리 수집"""
    try:
        url = "https://finance.naver.com/marketindex/interestDailyQuote.nhn?marketindexCd=IRR_KTB3Y"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        # HTML에서 마지막 종가 추출 (간단 정규식)
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
    try:
        from scanner_main import send_error_alert
        await send_error_alert(f"📊 거시 데이터 수집 위기", error_msg)
    except Exception:
        pass


# ============================================================
# 메인 수집기 (비동기)
# ============================================================
async def fetch_macro_data(force: bool = False) -> MacroData:
    global _cached_macro, _last_fetch_time, _consecutive_failures

    now = time.time()
    if not force and _cached_macro and (now - _last_fetch_time < 600):
        logger.debug("📊 거시 데이터 캐시 사용 (10분 이내)")
        return _cached_macro

    logger.info("📊 글로벌 거시 데이터 수집 시작...")
    debug_tower.log("SYSTEM", "MACRO_FETCH_START", {})

    data = _cached_macro if _cached_macro else MacroData()
    success = False
    loop = asyncio.get_running_loop()

    try:
        # 1. KOSPI
        kospi = await loop.run_in_executor(None, _fetch_yahoo, "^KS200", "5d")
        if kospi is not None:
            data.kospi_trend = kospi
            logger.info(f"   ✅ KOSPI: {kospi:.2f}%")
        else:
            logger.warning("   ⚠️ KOSPI 수집 실패")

        # 2. USD/KRW
        usd = await loop.run_in_executor(None, _fetch_yahoo, "KRW=X", "1d")
        if usd and usd > 0:
            data.usdkrw = usd
            logger.info(f"   ✅ USD/KRW: {usd:.2f}")

        # 3. VIX
        vix = await loop.run_in_executor(None, _fetch_yahoo, "^VIX", "1d")
        if vix and vix > 0:
            data.vix = vix
            data.vkospi = vix * 0.8
            logger.info(f"   ✅ VIX: {vix:.2f}")

        # 4. 미국 10년물 금리
        bond = await loop.run_in_executor(None, _fetch_yahoo, "^TNX", "1d")
        if bond and bond > 0:
            data.bond_3y = bond
            logger.info(f"   ✅ US 10Y: {bond:.2f}%")

        # 🔥 5. S&P 500
        spx = await loop.run_in_executor(None, _fetch_yahoo, "^GSPC", "5d")
        if spx is not None:
            data.spx_trend = spx
            logger.info(f"   ✅ S&P 500: {spx:.2f}%")

        # 🔥 6. 나스닥 100
        ndx = await loop.run_in_executor(None, _fetch_yahoo, "^NDX", "5d")
        if ndx is not None:
            data.ndx_trend = ndx
            logger.info(f"   ✅ 나스닥: {ndx:.2f}%")

        # 🔥 7. 필라델피아 반도체
        sox = await loop.run_in_executor(None, _fetch_yahoo, "^SOX", "5d")
        if sox is not None:
            data.sox_trend = sox
            logger.info(f"   ✅ SOX: {sox:.2f}%")

        # 🔥 8. WTI 원유
        oil = await loop.run_in_executor(None, _fetch_yahoo, "CL=F", "1d")
        if oil and oil > 0:
            data.oil_price = oil
            logger.info(f"   ✅ WTI: ${oil:.2f}")

        # 🔥 9. 한국 국채 3년물 (KTB)
        ktb = await loop.run_in_executor(None, _fetch_ktb_yield)
        if ktb and ktb > 0:
            data.ktb_3y = ktb
            logger.info(f"   ✅ KTB 3Y: {ktb:.2f}%")

        data.last_update = datetime.now().isoformat()
        _cached_macro = data
        _last_fetch_time = time.time()
        _consecutive_failures = 0
        success = True
        logger.info("📊 글로벌 거시 데이터 수집 완료")
        debug_tower.log("SYSTEM", "MACRO_FETCH_SUCCESS", data.to_dict())

    except Exception as e:
        _consecutive_failures += 1
        logger.error(f"❌ 거시 수집 실패 ({_consecutive_failures}회): {e}")
        debug_tower.capture_snapshot("SYSTEM", e, "MACRO_FETCH")
        if _consecutive_failures >= 3:
            await _send_alert(f"{_consecutive_failures}회 연속 실패: {str(e)}")

    return _cached_macro if _cached_macro else MacroData()


def get_cached_macro() -> MacroData:
    return _cached_macro if _cached_macro else MacroData()


async def refresh_macro_if_needed(force: bool = False) -> MacroData:
    return await fetch_macro_data(force=force)