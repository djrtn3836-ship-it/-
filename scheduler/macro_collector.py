"""
scheduler/macro_collector.py - 거시 데이터 수집기 v1.1 (장애 알림 추가)
- Yahoo Finance에서 실시간 거시 지표 수집
- 수집 실패 시 Telegram 경고 발송 (scanner_main.py의 send_error_alert 활용)
- 10분 TTL 캐싱
- 🔥 신규: 연속 실패 카운트 및 경고 알림 기능 추가
"""

import asyncio
import time
from datetime import datetime
from typing import Optional, Dict
from dataclasses import dataclass

from core.logger import setup_logger
from core.debug_tower import debug_tower

logger = setup_logger("macro_collector")

# ============================================================
# 데이터 클래스
# ============================================================
@dataclass
class MacroData:
    kospi_trend: float = 0.0
    usdkrw: float = 1300.0
    bond_3y: float = 3.5
    vix: float = 20.0
    vkospi: float = 20.0
    foreigner_futures: float = 0.0
    last_update: str = ""

    def to_dict(self) -> Dict:
        return {
            "kospi_trend": self.kospi_trend,
            "usdkrw": self.usdkrw,
            "bond_3y": self.bond_3y,
            "vix": self.vix,
            "vkospi": self.vkospi,
            "foreigner_futures": self.foreigner_futures,
            "last_update": self.last_update,
        }


# ============================================================
# 전역 변수
# ============================================================
_cached_macro: Optional[MacroData] = None
_last_fetch_time: float = 0
_consecutive_failures: int = 0
_LAST_ALERT_TIME: float = 0
_ALERT_COOLDOWN = 1800  # 30분 재발 방지


# ============================================================
# 수집 함수
# ============================================================
def _fetch_yahoo_sync(symbol: str, period: str = "5d") -> Optional[float]:
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period)
        if hist.empty:
            return None
        latest = hist['Close'].iloc[-1]
        if period == "5d" and len(hist) >= 2:
            old = hist['Close'].iloc[0]
            return (latest - old) / old * 100
        return float(latest)
    except Exception as e:
        logger.debug(f"Yahoo Finance 오류 ({symbol}): {e}")
        return None


async def _send_alert(error_msg: str) -> None:
    """Telegram 경고 전송 (scanner_main의 send_error_alert 사용)"""
    global _LAST_ALERT_TIME
    now = time.time()
    if now - _LAST_ALERT_TIME < _ALERT_COOLDOWN:
        return  # 30분 내 중복 경고 방지
    _LAST_ALERT_TIME = now
    try:
        # scanner_main.py의 send_error_alert를 동적으로 import
        from scanner_main import send_error_alert
        await send_error_alert(f"📊 거시 데이터 수집 실패", error_msg)
    except Exception as e:
        logger.warning(f"경고 전송 실패: {e}")


async def fetch_macro_data(force: bool = False) -> MacroData:
    global _cached_macro, _last_fetch_time, _consecutive_failures

    now = time.time()
    if not force and _cached_macro is not None and (now - _last_fetch_time < 600):
        logger.debug("📊 거시 데이터 캐시 사용 (10분 이내)")
        return _cached_macro

    logger.info("📊 거시 데이터 수집 시작...")
    debug_tower.log("SYSTEM", "MACRO_FETCH_START", {})

    if _cached_macro:
        data = _cached_macro
    else:
        data = MacroData()
        data.last_update = "초기값"

    success = False
    try:
        loop = asyncio.get_running_loop()

        # 1. KOSPI 200 추세
        kospi = await loop.run_in_executor(None, _fetch_yahoo_sync, "^KS200", "5d")
        if kospi is not None:
            data.kospi_trend = kospi
            logger.info(f"   ✅ KOSPI 5일 수익률: {kospi:.2f}%")
        else:
            kospi = await loop.run_in_executor(None, _fetch_yahoo_sync, "KOSPI", "5d")
            if kospi is not None:
                data.kospi_trend = kospi
                logger.info(f"   ✅ KOSPI (대체) 5일 수익률: {kospi:.2f}%")
            else:
                logger.warning("   ⚠️ KOSPI 수집 실패, 이전값 유지")

        # 2. USD/KRW
        usdkrw = await loop.run_in_executor(None, _fetch_yahoo_sync, "KRW=X", "1d")
        if usdkrw is not None and usdkrw > 0:
            data.usdkrw = usdkrw
            logger.info(f"   ✅ USD/KRW: {usdkrw:.2f}")

        # 3. VIX
        vix = await loop.run_in_executor(None, _fetch_yahoo_sync, "^VIX", "1d")
        if vix is not None and vix > 0:
            data.vix = vix
            data.vkospi = vix * 0.8
            logger.info(f"   ✅ VIX: {vix:.2f}, VKOSPI 추정: {data.vkospi:.2f}")

        # 4. 금리
        bond = await loop.run_in_executor(None, _fetch_yahoo_sync, "^TNX", "1d")
        if bond is not None and bond > 0:
            data.bond_3y = bond
            logger.info(f"   ✅ 미국 10년물 금리: {bond:.2f}%")
        else:
            bond_alt = await loop.run_in_executor(None, _fetch_yahoo_sync, "^IRX", "1d")
            if bond_alt is not None and bond_alt > 0:
                data.bond_3y = bond_alt
                logger.info(f"   ✅ 미국 3개월 금리 (대체): {bond_alt:.2f}%")

        data.last_update = datetime.now().isoformat()
        _cached_macro = data
        _last_fetch_time = time.time()
        _consecutive_failures = 0  # 성공 시 카운터 리셋
        success = True

        logger.info(f"📊 거시 데이터 수집 완료 (업데이트: {data.last_update})")
        debug_tower.log("SYSTEM", "MACRO_FETCH_SUCCESS", data.to_dict())

    except Exception as e:
        _consecutive_failures += 1
        logger.error(f"❌ 거시 데이터 수집 실패 ({_consecutive_failures}회 연속): {e}")
        debug_tower.capture_snapshot("SYSTEM", e, "MACRO_FETCH")

        # 🔥 3회 연속 실패 시 경고
        if _consecutive_failures >= 3:
            await _send_alert(f"거시 데이터 수집 {_consecutive_failures}회 연속 실패 (마지막 오류: {str(e)})")
            logger.critical(f"🚨 거시 데이터 3회 연속 수집 실패! 수동 확인 필요")

    # Fallback: 실패해도 이전 캐시 유지
    if _cached_macro is None:
        return MacroData()
    return _cached_macro


def get_cached_macro() -> MacroData:
    global _cached_macro
    if _cached_macro is None:
        return MacroData()
    return _cached_macro


async def refresh_macro_if_needed(force: bool = False) -> MacroData:
    return await fetch_macro_data(force=force)