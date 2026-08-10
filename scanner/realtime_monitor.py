"""
scanner/realtime_monitor.py - v5.1.2 FINAL
실시간 WebSocket 데이터 수신 및 조건 감지 (자동복구 + 재구독 지원)
"""

import asyncio
import time
from collections import deque
from typing import Dict, List, Optional, Any
from datetime import datetime

from core.logger import setup_logger
from data.stock_universe import get_universe  # 종목 리스트 로드 (없으면 기본값 사용)

logger = setup_logger("monitor")


class RealtimeMonitor:
    """실시간 WebSocket 데이터 모니터 (자동 복구 내장)"""

    # 기본 감시 종목 (universe 로드 실패 시 fallback)
    DEFAULT_TICKERS = ["005930", "000660", "035420"]

    def __init__(self, kiwoom_connector):
        """
        Args:
            kiwoom_connector: KiwoomConnectorV512 인스턴스
        """
        self.kiwoom = kiwoom_connector
        self._handler = self._on_data  # WebSocket 데이터 수신 핸들러

        # ============================================================
        # 🔥 [신규] 자동복구를 위한 구독 종목 저장소
        # ============================================================
        self._subscribed_tickers: List[str] = []  # 성공적으로 구독 등록된 종목 목록

        # 실시간 데이터 저장소 (최근 100개 데이터 유지)
        self._latest_data: Dict[str, Dict] = {}          # 최신 1건
        self._history: Dict[str, deque] = {}             # 히스토리 (최대 100개)
        self._history_limit = 100

        # 감지 신호 임계값 (추후 config에서 로드 가능)
        self.thresholds = {
            "price_change_ratio": 0.02,   # 2% 이상 변동 시 감지
            "volume_spike_ratio": 1.5,    # 평균 대비 1.5배 이상 거래량
        }

        # 스캔 상태
        self._is_running = False
        self._last_scan_time = 0

        # 종목 목록 (초기화 시 로드)
        self.tickers: List[str] = []

    # ============================================================
    # 1. 시작 및 구독 등록
    # ============================================================
    async def start(self):
        """모니터 시작: 종목 로드 → WebSocket 구독 등록"""
        if self._is_running:
            logger.warning("⚠️ 모니터가 이미 실행 중입니다.")
            return

        logger.info("📡 RealtimeMonitor 시작 중...")

        # 1) 종목 리스트 로드
        try:
            universe = get_universe()  # stock_universe.py에서 2300+ 종목 로드
            self.tickers = list(universe.keys())[:10]  # 테스트용 상위 10개 (전체를 원하면 [:])
            if not self.tickers:
                raise ValueError("Universe is empty")
            logger.info(f"📊 Universe 로드 완료: {len(self.tickers)}개 종목")
        except Exception as e:
            logger.warning(f"⚠️ Universe 로드 실패 ({e}), 기본 종목 {self.DEFAULT_TICKERS} 사용")
            self.tickers = self.DEFAULT_TICKERS

        # 2) 각 종목 WebSocket 구독 등록 (REG 패킷 전송)
        self._subscribed_tickers.clear()  # 기존 목록 초기화
        for ticker in self.tickers:
            try:
                await self.kiwoom.register_realtime(ticker, self._handler)
                self._subscribed_tickers.append(ticker)  # 🔥 성공한 종목 저장
                await asyncio.sleep(0.1)  # REG 요청 간격 (서버 부하 방지)
            except Exception as e:
                logger.error(f"❌ {ticker} 구독 실패: {e}")

        self._is_running = True
        logger.info(f"✅ RealtimeMonitor 시작 완료 (구독 종목: {len(self._subscribed_tickers)}개)")

    # ============================================================
    # 2. WebSocket 데이터 수신 핸들러
    # ============================================================
    def _on_data(self, data: Dict):
        """
        WebSocket에서 수신된 데이터를 처리하는 콜백
        - kiwoom_connector.register_realtime()에서 호출됨
        """
        try:
            # 키움 응답 형식에 따라 ticker 추출
            ticker = data.get('ticker') or data.get('symbol') or data.get('item')
            if not ticker:
                logger.warning(f"⚠️ 식별자 없는 데이터 수신: {data}")
                return

            # 현재가 추출 (data 구조에 따라 다를 수 있음)
            price = data.get('price') or data.get('cur_prc') or data.get('last')
            if price:
                try:
                    price = float(price)
                except (ValueError, TypeError):
                    price = 0.0

            # 거래량 추출
            volume = data.get('volume') or data.get('acc_vol') or 0
            try:
                volume = int(volume)
            except (ValueError, TypeError):
                volume = 0

            # 데이터 정리
            parsed = {
                'ticker': ticker,
                'price': price,
                'volume': volume,
                'timestamp': data.get('timestamp', time.time()),
                'raw': data
            }

            # 최신 데이터 저장
            self._latest_data[ticker] = parsed

            # 히스토리 저장
            if ticker not in self._history:
                self._history[ticker] = deque(maxlen=self._history_limit)
            self._history[ticker].append(parsed)

        except Exception as e:
            logger.error(f"❌ 데이터 핸들링 오류: {e}")

    # ============================================================
    # 3. 신호 스캔 (실시간 조건 감지)
    # ============================================================
    async def scan(self) -> List[Dict]:
        """
        현재 저장된 실시간 데이터를 기반으로 매매 신호를 감지합니다.
        Returns:
            감지된 종목 리스트 (각 항목: ticker, price, positives 등)
        """
        if not self._is_running:
            logger.warning("⚠️ 모니터가 실행 중이 아닙니다.")
            return []

        detected = []
        current_time = time.time()

        for ticker, data in self._latest_data.items():
            price = data.get('price', 0)
            if price <= 0:
                continue

            # 히스토리 조회 (이전 가격 비교)
            history = self._history.get(ticker, [])
            if len(history) < 2:
                continue

            prev_data = history[-2]
            prev_price = prev_data.get('price', price)
            if prev_price <= 0:
                continue

            # 변동률 계산
            change_ratio = (price - prev_price) / prev_price

            # === 조건 1: 급등/급락 (2% 이상) ===
            if abs(change_ratio) >= self.thresholds["price_change_ratio"]:
                action = "BUY" if change_ratio > 0 else "SELL"
                positives = ["급등 감지"] if change_ratio > 0 else ["급락 감지"]

                # 추가 분석용 데이터 구성
                stock_info = {
                    "ticker": ticker,
                    "price": price,
                    "action": action,
                    "score": min(1.0, abs(change_ratio) * 10),  # 변동률 기반 점수
                    "confidence": min(0.9, 0.5 + abs(change_ratio) * 5),
                    "positives": positives + [f"변동률: {change_ratio:+.2%}"],
                    "negatives": ["시장 변동성 주의"],
                    "timestamp": current_time,
                    "momentum": change_ratio,  # 분석기에서 활용
                    "volume": data.get('volume', 0),
                    "regime": "Sideways",  # 추후 MacroFilter 연동 가능
                    "flow": {},
                    "name": f"종목_{ticker}"  # fallback (DeepAnalyzer가 name을 덮어씀)
                }
                detected.append(stock_info)

        self._last_scan_time = current_time
        return detected

    # ============================================================
    # 4. 🔥 [신규] 재연결 시 전체 종목 재구독 (자동복구 핵심)
    # ============================================================
    async def resubscribe_all(self):
        """
        WebSocket이 재연결된 후, 기존에 구독했던 모든 종목을 다시 REG(구독) 요청합니다.
        - scanner_main.py의 reconnect_and_resubscribe()에서 호출됩니다.
        """
        if not self._subscribed_tickers:
            logger.warning("⚠️ 재구독할 종목 목록이 비어 있습니다.")
            return

        logger.info(f"🔄 저장된 {len(self._subscribed_tickers)}개 종목 재구독 시작...")

        # 히스토리/최신 데이터는 유지 (재구독 후 다시 채워짐)
        for ticker in self._subscribed_tickers:
            try:
                await self.kiwoom.register_realtime(ticker, self._handler)
                logger.debug(f"📡 재구독 완료: {ticker}")
                await asyncio.sleep(0.05)  # 서버 부하 방지
            except Exception as e:
                logger.error(f"❌ 재구독 실패 ({ticker}): {e}")

        logger.info(f"✅ 전체 {len(self._subscribed_tickers)}개 종목 재구독 완료")

    # ============================================================
    # 5. 상태 및 유틸리티
    # ============================================================
    def get_latest_price(self, ticker: str) -> Optional[float]:
        """특정 종목의 최신가 조회"""
        data = self._latest_data.get(ticker)
        return data.get('price') if data else None

    def get_subscribed_count(self) -> int:
        """현재 구독 중인 종목 수"""
        return len(self._subscribed_tickers)

    def is_running(self) -> bool:
        return self._is_running

    async def stop(self):
        """모니터 중지 (구독 해제)"""
        self._is_running = False
        for ticker in self._subscribed_tickers:
            await self.kiwoom.unregister_realtime(ticker)
        self._subscribed_tickers.clear()
        self._latest_data.clear()
        self._history.clear()
        logger.info("🛑 RealtimeMonitor 중지 완료")