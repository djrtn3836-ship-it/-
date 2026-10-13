"""scheduler/daily_collector.py - v2.0 (2026-10-12 야간 긴급 수정)

🔴 사고: v1.1은 Kiwoom ka10060(수급 차트) 응답을 OHLCV로 오파싱했다.
   - cur_prc 부호 접두("-251000") → 하락 종목 close<=0 → "비거래일"로 대량 오판
   - open/high/low/vol 필드 자체가 없어 저장돼도 0 (삼성전자 실측으로 발견)
   - '어제' 라벨 고정 → 월요일 수집분이 일요일 라벨(주말·휴일 전부 오라벨)

v2.0: 검증된 yfinance 경로(OHLCVBackfiller — 5년 백필과 동일 소스)를 1차로
   사용해 **응답의 실제 날짜**로 저장한다. Kiwoom 일봉은 2차 폴백(부호 제거 수정됨).
   수집 실패가 거래일인데 절반 이상이면 ERROR + CollectorStatus 실패 기록(무음 금지).
"""

import asyncio
from datetime import datetime

from collector.collector_status import collector_status
from core.logger import setup_logger
from core.holiday_utils import is_trading_day
from data.db_manager import DatabaseManager
from data.kiwoom_connector import KiwoomConnectorV512

logger = setup_logger("daily_collector")

collector_status.register("ohlcv_collector", freshness_seconds=86400)
COLLECT_PERIOD = "7d"  # 오늘 포함 최근 7일 — 하루 이틀 지연 수집돼도 자동 복구


async def _kiwoom_fallback(kiwoom: KiwoomConnectorV512, ticker: str) -> dict:
    """yfinance 실패 종목 1건을 Kiwoom 일봉(수정된 파서)으로 시도."""
    try:
        if kiwoom is None or not kiwoom.is_connected():
            return {}
        resp = await kiwoom.request_tr(ticker, "일봉")
        if resp and not resp.get("error") and float(resp.get("close") or 0) > 0:
            return resp
    except Exception as e:
        logger.debug(f"{ticker} Kiwoom 폴백 실패: {e}")
    return {}


async def collect_daily_ohlcv(
    kiwoom: KiwoomConnectorV512, db: DatabaseManager, tickers: list[str]
) -> None:
    """구독 종목들의 최근 일봉(최대 7일 창)을 수집해 DB에 저장(날짜별 멱등 upsert)."""
    if not tickers:
        logger.warning("⚠️ 수집할 종목 목록이 비어 있습니다.")
        return

    logger.info(f"📊 OHLCV 데이터 수집 시작(v2): {len(tickers)}개 종목")
    from scheduler.ohlcv_backfill import OHLCVBackfiller

    backfiller = OHLCVBackfiller(delay=0.2)
    success = 0
    failed: list[str] = []

    for i, ticker in enumerate(tickers, 1):
        rows, suffix = await asyncio.to_thread(backfiller.fetch_history, ticker, COLLECT_PERIOD)
        saved = 0
        if rows:
            saved = await db.save_ohlcv_batch(OHLCVBackfiller.to_records(ticker, rows))
        if not saved:
            fb = await _kiwoom_fallback(kiwoom, ticker)
            if fb:
                bar_date = str(fb.get("date") or "")[:10] or datetime.now().strftime("%Y-%m-%d")
                saved = await db.save_ohlcv_batch([(
                    ticker, bar_date,
                    fb.get("open", 0.0), fb.get("high", 0.0), fb.get("low", 0.0),
                    fb.get("close", 0.0), int(fb.get("volume", 0) or 0),
                )])
        if saved:
            success += 1
            collector_status.record_success("ohlcv_collector", {"ticker": ticker, "rows": saved})
        else:
            failed.append(ticker)
        if i % 50 == 0:
            logger.info(f"  … 진행 {i}/{len(tickers)} (성공 {success})")

    logger.info(f"✅ OHLCV 수집 완료(v2): {success}/{len(tickers)}종목 저장")
    if failed and is_trading_day() and len(failed) > len(tickers) // 2:
        msg = f"거래일인데 {len(failed)}/{len(tickers)}종목 수집 실패(예: {failed[:3]})"
        logger.error(f"❌ {msg}")
        collector_status.record_failure("ohlcv_collector", msg)
