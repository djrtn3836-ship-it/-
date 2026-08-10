"""
scheduler/daily_collector.py - OHLCV 데이터 자동 수집 (매일 16:30)
- ka10060 API로 OHLCV 전체 저장 (open, high, low, close, volume)
- 에러 발생 시 개별 종목 스킵하고 계속 진행
"""
import asyncio
from datetime import datetime, timedelta
from typing import List
from core.logger import setup_logger
from data.db_manager import DatabaseManager
from data.kiwoom_connector import KiwoomConnectorV512

logger = setup_logger("daily_collector")

async def collect_daily_ohlcv(kiwoom: KiwoomConnectorV512, db: DatabaseManager, tickers: List[str]):
    """구독 종목들의 전일 OHLCV 데이터를 수집하여 DB에 저장"""
    if not tickers:
        logger.warning("⚠️ 수집할 종목 목록이 비어 있습니다.")
        return

    logger.info(f"📊 OHLCV 데이터 수집 시작: {len(tickers)}개 종목")
    
    # 어제 날짜 (장 종료 후이므로 전일 데이터 수집)
    yesterday = (datetime.now() - timedelta(days=1))
    date_str = yesterday.strftime("%Y-%m-%d")
    date_param = yesterday.strftime("%Y%m%d")

    # Kiwoom 연결 확인
    if not kiwoom.is_connected():
        logger.warning("⚠️ Kiwoom 연결 없음, 재연결 시도...")
        await kiwoom.connect()
        if not kiwoom.is_connected():
            logger.error("❌ Kiwoom 연결 실패, OHLCV 수집 중단")
            return

    success_count = 0
    for ticker in tickers:
        try:
            await asyncio.sleep(0.25)  # Rate Limit 준수 (초당 4회)
            
            # 🔥 ka10060으로 OHLCV 전체 조회
            resp = await kiwoom.request_tr(ticker, "일봉")
            
            if resp and isinstance(resp, dict):
                # 응답에서 OHLCV 추출
                ohlcv_data = {
                    'open': float(resp.get('open', 0)),
                    'high': float(resp.get('high', 0)),
                    'low': float(resp.get('low', 0)),
                    'close': float(resp.get('close', 0)),
                    'volume': int(resp.get('volume', 0))
                }
                
                # 유효성 검사: 종가가 0보다 커야 함
                if ohlcv_data['close'] > 0:
                    await db.save_ohlcv(ticker, date_str, ohlcv_data)
                    success_count += 1
                    logger.debug(f"✅ {ticker} OHLCV 저장: 종가 {ohlcv_data['close']:,.0f} ({date_str})")
                else:
                    logger.warning(f"⚠️ {ticker} OHLCV 데이터 없음 (비거래일 또는 API 오류)")
            else:
                logger.warning(f"⚠️ {ticker} API 응답 없음")
                
        except Exception as e:
            # 개별 종목 실패 시 로그만 남기고 계속 진행
            logger.error(f"❌ {ticker} OHLCV 수집 실패: {e}")
            continue

    logger.info(f"✅ OHLCV 수집 완료: {success_count}/{len(tickers)}개 ({date_str})")