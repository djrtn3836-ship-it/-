"""
scheduler/daily_collector.py - v1.1 FINAL (재시도 + CollectorStatus 연동)
- OHLCV 수집 실패 시 개별 종목 재시도 (최대 2회)
- CollectorStatusManager에 성공/실패 기록
"""

import asyncio
from datetime import datetime, timedelta

from collector.collector_status import collector_status
from core.logger import setup_logger
from data.db_manager import DatabaseManager
from data.kiwoom_connector import KiwoomConnectorV512

logger = setup_logger("daily_collector")

# 🔥 v1.1: CollectorStatus 등록
collector_status.register("ohlcv_collector", freshness_seconds=86400)  # 1일


async def collect_daily_ohlcv(kiwoom: KiwoomConnectorV512, db: DatabaseManager, tickers: list[str]):
    """구독 종목들의 전일 OHLCV 데이터를 수집하여 DB에 저장"""
    if not tickers:
        logger.warning("⚠️ 수집할 종목 목록이 비어 있습니다.")
        return

    logger.info(f"📊 OHLCV 데이터 수집 시작: {len(tickers)}개 종목")

    yesterday = datetime.now() - timedelta(days=1)
    date_str = yesterday.strftime("%Y-%m-%d")
    date_param = yesterday.strftime("%Y%m%d")

    if not kiwoom.is_connected():
        logger.warning("⚠️ Kiwoom 연결 없음, 재연결 시도...")
        await kiwoom.connect()
        if not kiwoom.is_connected():
            logger.error("❌ Kiwoom 연결 실패, OHLCV 수집 중단")
            collector_status.record_failure("ohlcv_collector", "Kiwoom 연결 실패")
            return

    success_count = 0
    for ticker in tickers:
        try:
            await asyncio.sleep(0.25)

            # 🔥 v1.1: 재시도 루프 (최대 2회)
            last_error = None
            for attempt in range(3):
                try:
                    resp = await kiwoom.request_tr(ticker, "일봉")
                    if resp and isinstance(resp, dict):
                        ohlcv_data = {
                            "open": float(resp.get("open", 0)),
                            "high": float(resp.get("high", 0)),
                            "low": float(resp.get("low", 0)),
                            "close": float(resp.get("close", 0)),
                            "volume": int(resp.get("volume", 0)),
                        }
                        if ohlcv_data["close"] > 0:
                            await db.save_ohlcv(ticker, date_str, ohlcv_data)
                            success_count += 1
                            logger.debug(f"✅ {ticker} OHLCV 저장: 종가 {ohlcv_data['close']:,.0f}")
                            collector_status.record_success("ohlcv_collector", {"ticker": ticker})
                            break
                        else:
                            logger.warning(f"⚠️ {ticker} OHLCV 데이터 없음 (비거래일)")
                            break
                    else:
                        last_error = "응답 없음"
                        logger.warning(f"⚠️ {ticker} API 응답 없음, 시도 {attempt+1}/3")
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"⚠️ {ticker} OHLCV 수집 실패 ({attempt+1}/3): {e}")

                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))

            if last_error and attempt == 2:
                collector_status.record_failure("ohlcv_collector", f"{ticker}: {last_error}")

        except Exception as e:
            logger.error(f"❌ {ticker} OHLCV 수집 실패: {e}")
            collector_status.record_failure("ohlcv_collector", f"{ticker}: {e}")
            continue

    logger.info(f"✅ OHLCV 수집 완료: {success_count}/{len(tickers)}개 ({date_str})")
