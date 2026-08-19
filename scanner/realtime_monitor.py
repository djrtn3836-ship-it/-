"""
scanner/realtime_monitor.py - v5.7.0 FINAL (REG 요청 최적화 + 재시도 강화)
- 등록 간격 0.05 → 0.15초로 증가 (초당 요청 수 제한 초과 방지)
- 1차 등록 후 실패한 종목을 수집하여 3초 후 2차 재등록 시도
- _register_with_retry 재시도 횟수 2→3회, 간격 1→2초
"""

import asyncio
import time
from collections import deque
from typing import Dict, List, Optional, Any, Tuple

from core.logger import setup_logger
from core.config import get_config
from data.stock_universe import get_universe
from core.debug_tower import debug_tower
from core.regime_manager import regime_manager

logger = setup_logger("monitor")
config = get_config()


class RealtimeMonitor:
    DEFAULT_TICKERS = ["005930", "000660", "035420"]

    def __init__(self, kiwoom_connector, message_queue: asyncio.Queue = None):
        self.kiwoom = kiwoom_connector
        self._handler = self._on_data
        self._subscribed_tickers: List[str] = []
        self._latest_data: Dict[str, Dict] = {}
        self._history: Dict[str, deque] = {}
        self._orderbook_history: Dict[str, deque] = {}
        self._history_limit = 100
        self._orderbook_limit = 50
        self._is_running = False
        self._last_scan_time = 0.0
        self.tickers: List[str] = []

        self._name_cache: Dict[str, str] = {}

        self._last_signal_time: Dict[str, float] = {}
        self._last_signal_action: Dict[str, str] = {}

        self._message_queue = message_queue or asyncio.Queue(maxsize=100000)

        self.price_change_ratio = config.get_float("price_change_ratio", 0.02)
        self.cooldown_seconds = config.get_int("cooldown_seconds", 300)
        self.emergency_threshold = config.get_float("emergency_threshold", 0.05)
        self.max_subscriptions = 500

    def _get_current_regime(self) -> str:
        return regime_manager.get_regime()

    async def start(self):
        if self._is_running:
            logger.warning("⚠️ 모니터가 이미 실행 중입니다.")
            return

        logger.info(f"📡 RealtimeMonitor 시작 중... (최대 {self.max_subscriptions}종목)")

        try:
            universe = get_universe()
            self.tickers = list(universe.keys())[:self.max_subscriptions]
            if not self.tickers:
                raise ValueError("Universe is empty")
            self._name_cache = universe
            logger.info(f"📊 Universe 로드 완료: {len(self.tickers)}개 종목")
            debug_tower.log("SYSTEM", "UNIVERSE_LOADED", {"count": len(self.tickers)})
        except Exception as e:
            logger.warning(f"⚠️ Universe 로드 실패 ({e}), 기본 종목 사용")
            debug_tower.capture_snapshot("SYSTEM", e, "UNIVERSE_LOAD")
            self.tickers = self.DEFAULT_TICKERS
            self._name_cache = {t: f"종목_{t}" for t in self.tickers}

        # ============================================================
        # 🔥 v5.7.0: REG 등록 최적화 (간격 증가 + 실패 재시도)
        # ============================================================
        REGISTER_INTERVAL = 0.3
        RETRY_INTERVAL = 0.2
        RETRY_DELAY = 3.0  # 1차 완료 후 재시도 전 대기 시간

        self._subscribed_tickers.clear()
        failed_tickers: List[str] = []

        # 1차 등록
        for idx, ticker in enumerate(self.tickers):
            try:
                # 🔥 기존 등록 로직 (성공/실패 구분)
                try:
                    await self.kiwoom.register_realtime(ticker, self._handler, types=["0B"])
                    self._subscribed_tickers.append(ticker)
                    logger.debug(f"✅ {ticker} 등록 성공 ({idx+1}/{len(self.tickers)})")
                except Exception as e:
                    logger.warning(f"⚠️ {ticker} 등록 실패: {e}")
                    failed_tickers.append(ticker)

                # 등록 간격 (0.15초)
                await asyncio.sleep(REGISTER_INTERVAL)

            except Exception as e:
                logger.error(f"❌ {ticker} 등록 중 오류: {e}")
                failed_tickers.append(ticker)

        logger.info(f"✅ 1차 등록 완료: 성공 {len(self._subscribed_tickers)}개, 실패 {len(failed_tickers)}개")

        # 2차 재등록 (실패한 종목만)
        if failed_tickers:
            logger.info(f"⏳ {len(failed_tickers)}개 종목 2차 재등록 시도 (3초 후)...")
            await asyncio.sleep(RETRY_DELAY)

            retry_success = 0
            for ticker in failed_tickers:
                try:
                    await self.kiwoom.register_realtime(ticker, self._handler, types=["0B"])
                    self._subscribed_tickers.append(ticker)
                    retry_success += 1
                    logger.debug(f"✅ {ticker} 재등록 성공")
                except Exception as e:
                    logger.warning(f"⚠️ {ticker} 재등록 실패 (최종): {e}")
                await asyncio.sleep(RETRY_INTERVAL)

            logger.info(f"✅ 2차 재등록 완료: 추가 성공 {retry_success}개, 최종 실패 {len(failed_tickers) - retry_success}개")

        self._is_running = True
        self._last_scan_time = time.time()
        logger.info(f"✅ RealtimeMonitor 시작 완료 (구독 종목: {len(self._subscribed_tickers)}개)")
        debug_tower.log("SYSTEM", "MONITOR_STARTED", {"count": len(self._subscribed_tickers)})

    def _on_data(self, data: Dict):
        try:
            ticker = data.get('ticker') or data.get('symbol') or data.get('item')
            if not ticker:
                return

            data_type = data.get('type')
            parsed = {'ticker': ticker, 'timestamp': data.get('timestamp', time.time()), 'raw': data}

            if data_type == '0B' or 'price' in data or 'cur_prc' in data:
                price = data.get('price') or data.get('cur_prc') or data.get('last')
                if price is None:
                    price = 0.0
                else:
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

                debug_tower.log(ticker, "MONITOR_RECV", {"price": price, "volume": volume})

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
                        except:
                            pass
                for i in range(1, 11):
                    if i == 1:
                        price_key, qty_key = 'sel_fpr_bid', 'sel_fpr_req'
                    else:
                        price_key, qty_key = f'sel_{i-1}th_pre_bid', f'sel_{i-1}th_pre_req'
                    price = data.get(price_key); qty = data.get(qty_key)
                    if price is not None and qty is not None:
                        try:
                            orderbook['asks'].append((float(price), int(qty)))
                        except:
                            pass
                parsed['orderbook'] = orderbook
                if ticker not in self._orderbook_history:
                    self._orderbook_history[ticker] = deque(maxlen=self._orderbook_limit)
                self._orderbook_history[ticker].append(parsed)
                debug_tower.log(ticker, "ORDERBOOK_RECV", {"bids": len(orderbook['bids']), "asks": len(orderbook['asks'])})

            else:
                parsed['raw_data'] = data

            if ticker in self._latest_data:
                self._latest_data[ticker].update(parsed)
            else:
                self._latest_data[ticker] = parsed

            try:
                self._message_queue.put_nowait(parsed)
            except asyncio.QueueFull:
                logger.warning(f"⚠️ 메시지 큐 가득 참 → 데이터 드롭 ({ticker})")
                debug_tower.log(ticker, "QUEUE_FULL", {"queue_size": self._message_queue.qsize()})

        except Exception as e:
            logger.error(f"❌ 데이터 핸들링 오류: {e}", exc_info=True)
            debug_tower.capture_snapshot(ticker, e, "MONITOR_HANDLER")

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

        regime = self._get_current_regime()

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

            if abs(change_ratio) >= self.price_change_ratio:
                action = "BUY" if change_ratio > 0 else "SELL"
                positives = ["급등 감지"] if change_ratio > 0 else ["급락 감지"]
                insight = ""
                if support_level and price > support_level:
                    insight += f" | 📈 지지선 {support_level:,.0f}원 상향 이탈"
                if resistance_level and price < resistance_level:
                    insight += f" | 📉 저항선 {resistance_level:,.0f}원 하향 이탈"

                last_time = self._last_signal_time.get(ticker, 0)
                last_action = self._last_signal_action.get(ticker, '')
                is_emergency = abs(change_ratio) > self.emergency_threshold

                if not is_emergency and last_action == action and (current_time - last_time) < self.cooldown_seconds:
                    continue

                self._last_signal_time[ticker] = current_time
                self._last_signal_action[ticker] = action

                stock_name = self._name_cache.get(ticker, ticker)

                score = min(0.95, 0.4 + abs(change_ratio) * 12.5)
                confidence = min(0.9, 0.5 + abs(change_ratio) * 5)

                detected.append({
                    "ticker": ticker,
                    "name": stock_name,
                    "price": price,
                    "entry_price": price,
                    "action": action,
                    "score": score,
                    "confidence": confidence,
                    "positives": positives + [f"변동률: {change_ratio:+.2%}{insight}"],
                    "negatives": ["시장 변동성 주의"],
                    "timestamp": current_time,
                    "momentum": change_ratio,
                    "volume": data.get('volume', 0),
                    "regime": regime,
                    "flow": {},
                    "support_level": support_level,
                    "resistance_level": resistance_level,
                    "imbalance": imbalance,
                    "pressure": pressure,
                })
                debug_tower.log(ticker, "SIGNAL_DETECTED", {"action": action, "change": change_ratio, "score": score, "regime": regime})

        self._last_scan_time = current_time
        return detected

    async def resubscribe_all(self):
        if not self._subscribed_tickers:
            return
        logger.info(f"🔄 저장된 {len(self._subscribed_tickers)}개 종목 재구독 시작...")
        debug_tower.log("SYSTEM", "RESUBSCRIBE_START", {"count": len(self._subscribed_tickers)})
        for ticker in self._subscribed_tickers:
            try:
                await self.kiwoom.register_realtime(ticker, self._handler, types=["0B"])
                await asyncio.sleep(0.15)  # 재구독 시에도 간격 유지
            except Exception as e:
                logger.error(f"❌ 재구독 실패 ({ticker}): {e}")
                debug_tower.capture_snapshot(ticker, e, "RESUBSCRIBE")
        logger.info(f"✅ 전체 종목 재구독 완료")
        debug_tower.log("SYSTEM", "RESUBSCRIBE_COMPLETE", {})

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
        debug_tower.log("SYSTEM", "MONITOR_STOPPED", {})