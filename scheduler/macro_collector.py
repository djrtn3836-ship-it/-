# -*- coding: utf-8 -*-
"""
scheduler/macro_collector.py - v2.4 (Session 30: mypy strict 적용)

v2.3 -> v2.4 변경 사항 (실제 mypy 오류 2건을 줄 번호 기준으로 정밀 대조하여 수정):
    - MacroData.to_dict(): 반환 타입 dict -> dict[str, Any] 명시 [type-arg]
    - _alert_callback/set_alert_callback: 콜백 타입을 Callable[[str, str], None]에서
      Callable[[str, str], Awaitable[None]]로 정정.
      실제로 등록되는 콜백(app/bootstrap.py의 _send_error_alert, scanner_main.py의
      send_error_alert)은 모두 async def이므로 호출 시 코루틴(Awaitable[None])을
      반환합니다. 기존 타입 선언은 "동기 함수가 None을 반환한다"고 잘못 기술하고
      있었고, _send_alert()가 이를 await하면서 "Incompatible types in await
      (actual type None, expected type Awaitable[Any])" 오류가 발생했습니다.
      Callable[..., Any]로 폭넓게 완화하거나 호출부에서 cast()로 경고만 억제하는
      대신, 실제 계약을 정확히 타입으로 표현했습니다. 이렇게 하면 향후 누군가
      실수로 동기 함수를 set_alert_callback()에 전달할 경우 mypy가 즉시 타입
      오류로 잡아내어, "동기 함수 등록 → await 시 런타임 크래시"라는 잠재적
      회귀를 사전에 차단합니다.
    - 로직/동작 100% 무변경 — 데이터 수집 로직/심볼은 v2.3과 완전히 동일
"""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from collector.collector_status import collector_status
from core.debug_tower import debug_tower
from core.logger import setup_logger

logger = setup_logger("macro_collector")

# ============================================================
# 콜백 패턴 (순환 참조 제거)
# ============================================================
# 🔧 Session 30: 실제로 등록되는 콜백은 항상 async def이므로
# Callable[[str, str], Awaitable[None]]로 정확히 명시 (await 호환 + 오용 방지)
_alert_callback: Callable[[str, str], Awaitable[None]] | None = None


def set_alert_callback(func: Callable[[str, str], Awaitable[None]]) -> None:
    """scanner_main/bootstrap에서 알림 함수를 등록 (반드시 async def 함수여야 함)"""
    global _alert_callback
    _alert_callback = func


@dataclass
class MacroData:
    kospi_trend: float = 0.0
    usdkrw: float = 1300.0
    bond_3y: float = 3.5          # ⚠️ 이름은 3y지만 실제로는 US 10Y 금리(원본 호환 유지)
    vix: float = 20.0
    vkospi: float = 20.0
    foreigner_futures: float = 0.0
    spx_trend: float = 0.0
    ndx_trend: float = 0.0
    sox_trend: float = 0.0
    oil_price: float = 75.0
    ktb_3y: float = 3.0
    # 🔥 P6-2 거시 지표 확장 (해외지수/금리/원자재)
    n225_trend: float = 0.0       # 니케이225 5일 수익률(%) — 아시아 동조
    dxy: float = 103.0            # 달러인덱스 — 강세면 신흥국 자금 유출
    us_2y: float = 4.0            # 미국 2년물 금리(%)
    yield_spread: float = 0.0     # 10Y − 2Y (음수 = 장단기 역전, 침체 선행)
    copper_price: float = 4.2     # 구리(경기 선행 지표, Dr. Copper)
    gold_price: float = 2400.0    # 금(위험회피 수요)
    last_update: str = ""

    def to_dict(self) -> dict[str, Any]:
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
            "n225_trend": self.n225_trend,
            "dxy": self.dxy,
            "us_2y": self.us_2y,
            "yield_spread": self.yield_spread,
            "copper_price": self.copper_price,
            "gold_price": self.gold_price,
            "last_update": self.last_update,
        }


_cached_macro: MacroData | None = None
_last_fetch_time: float = 0
_consecutive_failures: int = 0
_LAST_ALERT_TIME: float = 0
_ALERT_COOLDOWN = 1800


def _fetch_yahoo(symbol: str, period: str = "5d", as_trend: bool = False) -> float | None:
    """
    as_trend=True: 반드시 수익률(%)만 반환. 계산 불가 시 None
                   (이전 버전은 원본 가격을 그대로 반환하는 버그가 있었음)
    as_trend=False: 최신 종가(절대값)를 그대로 반환
    """
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period)
        if hist.empty:
            return None

        if as_trend:
            if len(hist) < 2:
                logger.debug(f"{symbol}: 추세 계산용 데이터 부족 ({len(hist)}행) → None 반환")
                return None
            old = hist["Close"].iloc[0]
            latest = hist["Close"].iloc[-1]
            if old == 0:
                return None
            return float((latest - old) / old * 100)

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
    """한국 국고채 3년 금리. 네이버(1순위) → FRED(2순위, 월간·지연)."""
    try:
        url = "https://finance.naver.com/marketindex/interestDailyQuote.nhn?marketindexCd=IRR_KTB3Y"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        match = re.search(r'<td class="num">([\d.]+)</td>', resp.text)
        if match:
            return float(match.group(1))
    except Exception as e:
        # 2026-10-01 확인: 네이버 엔드포인트가 HTTP 410(영구 종료) → FRED 폴백 사용
        logger.debug(f"KTB 수집 실패(네이버): {e}")

    value = _fetch_fred("IRLTLT01KRM156N")
    if value and value > 0:
        logger.debug(f"KTB 대체값 사용(FRED 월간 장기금리): {value}")
    return value


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
    loop = asyncio.get_running_loop()

    try:
        # 🔧 P6-2: ^KS200은 데이터 1행만 반환되어 추세 계산 불가(최고 가중치 지표 무음 사망)
        #    → ^KS11(코스피 종합) 1순위, ^KS200 폴백
        kospi = await loop.run_in_executor(None, _fetch_yahoo, "^KS11", "5d", True)
        if kospi is None:
            kospi = await loop.run_in_executor(None, _fetch_yahoo, "^KS200", "10d", True)
        if kospi is not None:
            data.kospi_trend = kospi
            logger.info(f"   ✅ KOSPI: {kospi:.2f}%")
        else:
            logger.warning(f"   ⚠️ KOSPI 수집 실패/데이터 부족, 이전값 유지 ({data.kospi_trend:.2f}%)")

        usd = await loop.run_in_executor(None, _fetch_yahoo, "KRW=X", "1d", False)
        if usd and usd > 0:
            data.usdkrw = usd
            logger.info(f"   ✅ USD/KRW: {usd:.2f}")

        vix = await loop.run_in_executor(None, _fetch_yahoo, "^VIX", "1d", False)
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

        bond = await loop.run_in_executor(None, _fetch_yahoo, "^TNX", "1d", False)
        if bond and bond > 0:
            data.bond_3y = bond
            logger.info(f"   ✅ US 10Y: {bond:.2f}%")
        else:
            bond_fallback = await loop.run_in_executor(None, _fetch_fred, "DGS10")
            if bond_fallback and bond_fallback > 0:
                data.bond_3y = bond_fallback
                logger.info(f"   ✅ US 10Y (FRED Fallback): {bond_fallback:.2f}%")

        spx = await loop.run_in_executor(None, _fetch_yahoo, "^GSPC", "5d", True)
        if spx is not None:
            data.spx_trend = spx
            logger.info(f"   ✅ S&P 500: {spx:.2f}%")

        ndx = await loop.run_in_executor(None, _fetch_yahoo, "^NDX", "5d", True)
        if ndx is not None:
            data.ndx_trend = ndx
            logger.info(f"   ✅ 나스닥: {ndx:.2f}%")

        sox = await loop.run_in_executor(None, _fetch_yahoo, "^SOX", "5d", True)
        if sox is not None:
            data.sox_trend = sox
            logger.info(f"   ✅ SOX: {sox:.2f}%")

        oil = await loop.run_in_executor(None, _fetch_yahoo, "CL=F", "1d", False)
        if oil and oil > 0:
            data.oil_price = oil
            logger.info(f"   ✅ WTI: ${oil:.2f}")

        ktb = await loop.run_in_executor(None, _fetch_ktb_yield)
        if ktb and ktb > 0:
            data.ktb_3y = ktb
            logger.info(f"   ✅ KTB 3Y: {ktb:.2f}%")

        # 🔥 P6-2 확장: 해외지수/금리/원자재
        n225 = await loop.run_in_executor(None, _fetch_yahoo, "^N225", "5d", True)
        if n225 is not None:
            data.n225_trend = n225
            logger.info(f"   ✅ 니케이225: {n225:.2f}%")

        dxy = await loop.run_in_executor(None, _fetch_yahoo, "DX-Y.NYB", "1d", False)
        if dxy and dxy > 0:
            data.dxy = dxy
            logger.info(f"   ✅ 달러인덱스: {dxy:.2f}")

        # 🔧 P6-2: DGS2(정확한 2년물) 1순위, ^IRX(13주물, 근사) 폴백
        us2y_fallback = await loop.run_in_executor(None, _fetch_fred, "DGS2")
        if us2y_fallback and us2y_fallback > 0:
            data.us_2y = us2y_fallback
        else:
            us2y = await loop.run_in_executor(None, _fetch_yahoo, "^IRX", "1d", False)
            if us2y and us2y > 0:
                data.us_2y = us2y
                logger.info(f"   ✅ US 2Y (^IRX 13주물 근사): {us2y:.2f}%")
        # 10Y − 2Y 스프레드(파생): 음수면 장단기 역전 = 침체 선행 신호
        spread = round(float(data.bond_3y) - float(data.us_2y), 3)
        data.yield_spread = spread if -3.0 <= spread <= 4.0 else 0.0
        logger.info(f"   ✅ US 2Y: {data.us_2y:.2f}% / 10Y-2Y: {data.yield_spread:+.2f}%p")

        copper = await loop.run_in_executor(None, _fetch_yahoo, "HG=F", "1d", False)
        if copper and copper > 0:
            data.copper_price = copper
            logger.info(f"   ✅ 구리: ${copper:.3f}/lb")

        gold = await loop.run_in_executor(None, _fetch_yahoo, "GC=F", "1d", False)
        if gold and gold > 0:
            data.gold_price = gold
            logger.info(f"   ✅ 금: ${gold:.0f}/oz")

        if data.vix < 0 or data.vix > 100:
            logger.warning(f"⚠️ VIX 이상치 감지: {data.vix:.2f} → 20.0으로 대체")
            data.vix = 20.0
            data.vkospi = 16.0

        data.last_update = datetime.now().isoformat()
        _cached_macro = data
        _last_fetch_time = time.time()
        _consecutive_failures = 0

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
