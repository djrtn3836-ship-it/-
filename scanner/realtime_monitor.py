"""
scanner/realtime_monitor.py - v5.3.2 (최적화: 변경된 종목만 스캔 + 키움 표준 호가 필드)
"""
import asyncio
import time
from collections import deque
from typing import Dict, List, Optional, Any

from core.logger import setup_logger
from data.stock_universe import get_universe

logger = setup_logger("monitor")

class RealtimeMonitor:
    DEFAULT_TICKERS = ["005930", "000660", "035420"]

    def __init__(self, kiwoom_connector):
        self.kiwoom = kiwoom_connector
        self._handler = self._on_data
        self._subscribed_tickers: List[str] = []
        self._latest_data: Dict[str, Dict] = {}
        self._history: Dict[str, deque] = {}
        self._orderbook_history: Dict[str, deque] = {}
        self._history_limit = 100
        self._orderbook_limit = 50
        self.thresholds = {"price_change_ratio": 0.02, "volume_spike_ratio": 1.5}
        self._is_running = False
        self._last_scan_time = 0.0  # 🔥 E: 마지막 스캔 시간
        self.tickers: List[str] = []

    async def start(self):
        if self._is_running:
            logger.warning("⚠️ 모니터가 이미 실행 중입니다.")
            return

        logger.info("📡 RealtimeMonitor 시작 중... (호가잔량 포함)")
        try:
            universe = get_universe()
            self.tickers = list(universe.keys())[:10]
            if not self.tickers:
                raise ValueError("Universe is empty")
            logger.info(f"📊 Universe 로드 완료: {len(self.tickers)}개 종목")
        except Exception as e:
            logger.warning(f"⚠️ Universe 로드 실패 ({e}), 기본 종목 사용")
            self.tickers = self.DEFAULT_TICKERS

        self._subscribed_tickers.clear()
        for ticker in self.tickers:
            try:
                await self.kiwoom.register_realtime(ticker, self._handler, types=["0B", "0A"])
                self._subscribed_tickers.append(ticker)
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"❌ {ticker} 구독 실패: {e}")

        self._is_running = True
        self._last_scan_time = time.time()
        logger.info(f"✅ RealtimeMonitor 시작 완료 (구독 종목: {len(self._subscribed_tickers)}개)")

    # ============================================================
    # 🔥 수정된 핵심: 키움 표준 호가 필드명 (buy_fpr_bid, sel_fpr_bid)
    # ============================================================
    def _on_data(self, data: Dict):
        try:
            ticker = data.get('ticker') or data.get('symbol') or data.get('item')
            if not ticker:
                return

            data_type = data.get('type')
            parsed = {'ticker': ticker, 'timestamp': data.get('timestamp', time.time()), 'raw': data}

            if data_type == '0B' or 'price' in data or 'cur_prc' in data:
                price = data.get('price') or data.get('cur_prc') or data.get('last')
                if price:
                    try:
                        price = float(price)
                    except:
                        price = 0.0
                volume = data.get('volume') or data.get('acc_vol') or 0
                try:
                    volume = int(volume)
                except:
                    volume = 0

                parsed['price'] = price
                parsed['volume'] = volume
                if ticker not in self._history:
                    self._history[ticker] = deque(maxlen=self._history_limit)
                self._history[ticker].append(parsed)

            elif data_type == '0A' or 'buy_fpr_bid' in data or 'sel_fpr_bid' in data:
                orderbook = {'bids': [], 'asks': []}
                # 매수 호가 (buy_fpr_bid, buy_1th_pre_bid ~ buy_9th_pre_bid)
                for i in range(1, 11):
                    if i == 1:
                        price_key, qty_key = 'buy_fpr_bid', 'buy_fpr_req'
                    else:
                        price_key, qty_key = f'buy_{i-1}th_pre_bid', f'buy_{i-1}th_pre_req'
                    price = data.get(price_key); qty = data.get(qty_key)
                    if price is not None and qty is not None:
                        try:
                            orderbook['bids'].append((float(price), int(qty)))
                        except: pass
                # 매도 호가 (sel_fpr_bid, sel_1th_pre_bid ~ sel_9th_pre_bid)
                for i in range(1, 11):
                    if i == 1:
                        price_key, qty_key = 'sel_fpr_bid', 'sel_fpr_req'
                    else:
                        price_key, qty_key = f'sel_{i-1}th_pre_bid', f'sel_{i-1}th_pre_req'
                    price = data.get(price_key); qty = data.get(qty_key)
                    if price is not None and qty is not None:
                        try:
                            orderbook['asks'].append((float(price), int(qty)))
                        except: pass
                parsed['orderbook'] = orderbook
                if ticker not in self._orderbook_history:
                    self._orderbook_history[ticker] = deque(maxlen=self._orderbook_limit)
                self._orderbook_history[ticker].append(parsed)

            else:
                parsed['raw_data'] = data

            if ticker in self._latest_data:
                self._latest_data[ticker].update(parsed)
            else:
                self._latest_data[ticker] = parsed

        except Exception as e:
            logger.error(f"❌ 데이터 핸들링 오류: {e}", exc_info=True)

    # ============================================================
    # 🔥 E: 변경된 종목만 스캔 (성능 최적화)
    # ============================================================
    async def scan(self) -> List[Dict]:
        if not self._is_running:
            return []

        detected = []
        current_time = time.time()
        changed_tickers = [
            ticker for ticker, data in self._latest_data.items()
            if data.get('timestamp', 0) > self._last_scan_time
        ]

        if not changed_tickers:
            return []

        for ticker in changed_tickers:
            data = self._latest_data.get(ticker, {})
            price = data.get('price', 0)
            if price <= 0:
                continue

            history = self._history.get(ticker, [])
            if len(history) < 2:
                continue

            prev_data = history[-2]
            prev_price = prev_data.get('price', price)
            if prev_price <= 0:
                continue

            change_ratio = (price - prev_price) / prev_price

            orderbook = data.get('orderbook', {})
            bids = orderbook.get('bids', [])
            asks = orderbook.get('asks', [])

            support_level = None
            resistance_level = None
            if bids:
                max_bid = max(bids, key=lambda x: x[1])
                support_level = max_bid[0]
            if asks:
                max_ask = max(asks, key=lambda x: x[1])
                resistance_level = max_ask[0]

            if abs(change_ratio) >= self.thresholds["price_change_ratio"]:
                action = "BUY" if change_ratio > 0 else "SELL"
                positives = ["급등 감지"] if change_ratio > 0 else ["급락 감지"]
                insight = ""
                if support_level and price > support_level:
                    insight = f" | 📈 지지선 {support_level:,.0f}원 상향 이탈"
                elif resistance_level and price < resistance_level:
                    insight = f" | 📉 저항선 {resistance_level:,.0f}원 하향 이탈"

                detected.append({
                    "ticker": ticker,
                    "price": price,
                    "action": action,
                    "score": min(1.0, abs(change_ratio) * 10),
                    "confidence": min(0.9, 0.5 + abs(change_ratio) * 5),
                    "positives": positives + [f"변동률: {change_ratio:+.2%}{insight}"],
                    "negatives": ["시장 변동성 주의"],
                    "timestamp": current_time,
                    "momentum": change_ratio,
                    "volume": data.get('volume', 0),
                    "regime": "Sideways",
                    "flow": {},
                    "name": f"종목_{ticker}",
                    "support_level": support_level,
                    "resistance_level": resistance_level,
                })

        self._last_scan_time = current_time
        return detected

    async def resubscribe_all(self):
        if not self._subscribed_tickers:
            logger.warning("⚠️ 재구독할 종목 목록이 비어 있습니다.")
            return

        logger.info(f"🔄 저장된 {len(self._subscribed_tickers)}개 종목 재구독 시작... (호가+체결)")
        for ticker in self._subscribed_tickers:
            try:
                await self.kiwoom.register_realtime(ticker, self._handler, types=["0B", "0A"])
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.error(f"❌ 재구독 실패 ({ticker}): {e}")
        logger.info(f"✅ 전체 종목 재구독 완료")

    def get_latest_price(self, ticker: str) -> Optional[float]:
        data = self._latest_data.get(ticker)
        return data.get('price') if data else None

    def get_orderbook(self, ticker: str) -> Optional[Dict]:
        data = self._latest_data.get(ticker)
        return data.get('orderbook') if data else None

    def get_subscribed_count(self) -> int:
        return len(self._subscribed_tickers)

    def is_running(self) -> bool:
        return self._is_running

    async def stop(self):
        self._is_running = False
        for ticker in self._subscribed_tickers:
            await self.kiwoom.unregister_realtime(ticker)
        self._subscribed_tickers.clear()
        self._latest_data.clear()
        self._history.clear()
        self._orderbook_history.clear()
        logger.info("🛑 RealtimeMonitor 중지 완료")