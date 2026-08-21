"""
scanner/emergency_recovery.py - v1.0 (WebSocket Fallback)
- WebSocket 60초 침묵 시 REST API 폴링으로 전환
- 현재가를 1분마다 조회하여 2% 변동 감지
"""

import asyncio
import time
from datetime import datetime

from core.logger import setup_logger
from data.kiwoom_connector import KiwoomConnectorV512
from report.telegram_sender import TelegramSender

logger = setup_logger("emergency")
telegram = TelegramSender()


class EmergencyRecovery:
    def __init__(self, kiwoom: KiwoomConnectorV512, tickers: list[str]):
        self.kiwoom = kiwoom
        self.tickers = tickers
        self.last_prices = {t: 0.0 for t in tickers}
        self.is_fallback = False
        self.fallback_start_time = None

    async def run_fallback(self):
        """REST API 폴링으로 현재가 수집 (WebSocket 대체)"""
        logger.warning("🚨 비상 모드 활성화: REST API 폴링 시작")
        await telegram.send_raw("🚨 WebSocket 침묵 감지 → REST API 폴링 모드로 전환합니다.")

        self.is_fallback = True
        self.fallback_start_time = datetime.now()

        while self.is_fallback:
            for ticker in self.tickers[:50]:  # 50개만 폴링 (Rate Limit 고려)
                try:
                    result = await self.kiwoom.request_tr(ticker, "현재가")
                    if result and "close" in result:
                        price = float(result["close"])
                        prev_price = self.last_prices.get(ticker, price)
                        if prev_price > 0:
                            change = (price - prev_price) / prev_price
                            if abs(change) >= 0.02:
                                await telegram.send_raw(
                                    f"🚨 [Fallback] {ticker} {change*100:+.2f}% 변동 감지! (현재가: {price:,.0f}원)"
                                )
                        self.last_prices[ticker] = price
                except Exception as e:
                    logger.debug(f"Fallback TR 실패 ({ticker}): {e}")
                await asyncio.sleep(0.3)  # 0.3초 간격

            await asyncio.sleep(60)  # 1분마다 전체 스캔

    def stop_fallback(self):
        self.is_fallback = False
        logger.info("✅ 비상 모드 종료")