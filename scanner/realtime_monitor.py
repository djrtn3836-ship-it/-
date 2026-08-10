"""
scanner/realtime_monitor.py - v5.4.2 (ATR 전달)
- 신호 발생 시 DeepAnalyzer가 ATR 계산하도록 준비
- entry_price(진입가)도 함께 전달
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
        self.thresholds = {"price_change_ratio": 0.02}
        self._is_running = False
        self._last_scan_time = 0.0
        self.tickers: List[str] = []

        # 종목명 캐시
        self._name_cache: Dict[str, str] = {}

        # 신호 쿨링
        self._last_signal_time: Dict[str, float] = {}
        self._last_signal_action: Dict[str, str] = {}

    async def start(self):
        if self._is_running:
            logger.warning("⚠️ 모니터가 이미 실행 중입니다.")
            return

        logger.info("📡 RealtimeMonitor 시작 중... (ATR 연동)")
        try:
            universe = get_universe()
            self.tickers = list(universe.keys())[:10]
            if not self.tickers:
                raise ValueError("Universe is empty")
            self._name_cache = universe
            logger.info(f"📊 Universe 로드 완료: {len(self.tickers)}개 종목")
        except Exception as e:
            logger.warning(f"⚠️ Universe 로드 실패 ({e}), 기본 종목 사용")
            self.tickers = self.DEFAULT_TICKERS
            self._name_cache = {
                "005930": "삼성전자",
                "000660": "SK하이닉스",
                "035420": "NAVER",
            }

        self._subscribed_tickers.clear()
        for ticker in self.tickers:
            try:
                await self.kiwoom.register_realtime(ticker, self._handler, types=["0B"])
                self._subscribed_tickers.append(ticker)
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"❌ {ticker} 구독 실패: {e}")

        self._is_running = True
        self._last_scan_time = time.time()
        logger.info(f"✅ RealtimeMonitor 시작 완료 (구독 종목: {len(self._subscribed_tickers)}개)")

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

    def _calculate_imbalance(self, bids: List, asks: List) -> tuple:
        total_bid = sum(qty for _, qty in bids) if bids else 0
        total_ask = sum(qty for _, qty in asks) if asks else 0
        if total_bid + total_ask == 0:
            return 0.5, "⚖️ 데이터 없음"
        imbalance = total_bid / (total_bid + total_ask)
        if imbalance > 0.65:
            pressure = f"🔥 강한 매수 압력 ({imbalance:.1%})"
        elif imbalance < 0.35:
            pressure = f"💀 강한 매도 압력 ({imbalance:.1%})"
        else:
            pressure = f"⚖️ 중립 ({imbalance:.1%})"
        return imbalance, pressure

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

            imbalance, pressure = self._calculate_imbalance(bids, asks)

            if abs(change_ratio) >= self.thresholds["price_change_ratio"]:
                action = "BUY" if change_ratio > 0 else "SELL"
                positives = ["급등 감지"] if change_ratio > 0 else ["급락 감지"]
                insight = ""
                if support_level and price > support_level:
                    insight += f" | 📈 지지선 {support_level:,.0f}원 상향 이탈"
                if resistance_level and price < resistance_level:
                    insight += f" | 📉 저항선 {resistance_level:,.0f}원 하향 이탈"

                # 신호 쿨링
                last_time = self._last_signal_time.get(ticker, 0)
                last_action = self._last_signal_action.get(ticker, '')
                is_emergency = abs(change_ratio) > 0.05

                if not is_emergency and last_action == action and (current_time - last_time) < 300:
                    logger.debug(f"⏳ {ticker} {action} 쿨링 중")
                    continue

                self._last_signal_time[ticker] = current_time
                self._last_signal_action[ticker] = action

                stock_name = self._name_cache.get(ticker, ticker)

                # 🔥 ATR은 DeepAnalyzer가 DB에서 계산하므로, 여기서는 entry_price만 전달
                stock_info = {
                    "ticker": ticker,
                    "name": stock_name,
                    "price": price,
                    "entry_price": price,  # 진입가 = 현재가 (신호 발생 시점)
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
                    "support_level": support_level,
                    "resistance_level": resistance_level,
                    "imbalance": imbalance,
                    "pressure": pressure,
                }
                detected.append(stock_info)

        self._last_scan_time = current_time
        return detected

    async def resubscribe_all(self):
        if not self._subscribed_tickers:
            logger.warning("⚠️ 재구독할 종목 목록이 비어 있습니다.")
            return
        logger.info(f"🔄 저장된 {len(self._subscribed_tickers)}개 종목 재구독 시작...")
        for ticker in self._subscribed_tickers:
            try:
                await self.kiwoom.register_realtime(ticker, self._handler, types=["0B"])
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.error(f"❌ 재구독 실패 ({ticker}): {e}")
        logger.info(f"✅ 전체 종목 재구독 완료")

    def get_latest_price(self, ticker: str) -> Optional[float]:
        data = self._latest_data.get(ticker)
        return data.get('price') if data else None

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