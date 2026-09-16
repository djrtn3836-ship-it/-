# -*- coding: utf-8 -*-
"""
scanner/realtime_monitor.py - v5.7.1 (Session 38: mypy strict ?곸슜 + ?좎옱 踰꾧렇 ?섏젙)
- ?뵩 _on_data() ?덉쇅 泥섎━遺?먯꽌 ticker 蹂?섍? ?꾩쭅 ?좊떦?섏? ?딆? ?곹깭濡??덉쇅媛
  諛쒖깮??寃쎌슦 諛쒖깮?섎뜕 ?좎옱??UnboundLocalError瑜??덉쟾???대갚 蹂?섎줈 ?쒓굅
- 紐⑤뱺 硫붿꽌??諛섑솚 ????쒕꽕由????紐낆떆, 洹???濡쒖쭅 100% 臾대?寃?"""

import asyncio
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from core.config import get_config
from core.debug_tower import debug_tower
from core.logger import setup_logger
from core.regime_manager import regime_manager
from data.stock_universe import get_universe

logger = setup_logger("monitor")
config = get_config()


class RealtimeMonitor:
    DEFAULT_TICKERS: List[str] = ["005930", "000660", "035420"]

    def __init__(self, kiwoom_connector: Any, message_queue: Optional["asyncio.Queue[Dict[str, Any]]"] = None) -> None:
        self.kiwoom: Any = kiwoom_connector
        self._handler = self._on_data
        self._subscribed_tickers: List[str] = []
        self._latest_data: Dict[str, Dict[str, Any]] = {}
        self._history: Dict[str, Deque[Dict[str, Any]]] = {}
        self._orderbook_history: Dict[str, Deque[Dict[str, Any]]] = {}
        self._history_limit: int = 100
        self._orderbook_limit: int = 50
        self._is_running: bool = False
        self._last_scan_time: float = 0.0
        self.tickers: List[str] = []

        self._name_cache: Dict[str, str] = {}

        self._last_signal_time: Dict[str, float] = {}
        self._last_signal_action: Dict[str, str] = {}

        self._message_queue: "asyncio.Queue[Dict[str, Any]]" = message_queue or asyncio.Queue(maxsize=100000)

        self.price_change_ratio: float = config.get_float("price_change_ratio", 0.02)
        self.cooldown_seconds: int = config.get_int("cooldown_seconds", 300)
        self.emergency_threshold: float = config.get_float("emergency_threshold", 0.05)
        self.max_subscriptions: int = config.get_int("max_subscriptions", 200)

    def _get_current_regime(self) -> str:
        return str(regime_manager.get_regime())

    async def start(self) -> None:
        if self._is_running:
            logger.warning("?좑툘 紐⑤땲?곌? ?대? ?ㅽ뻾 以묒엯?덈떎.")
            return

        logger.info(f"?뱻 RealtimeMonitor ?쒖옉 以?.. (理쒕? {self.max_subscriptions}醫낅ぉ)")

        try:
            universe = get_universe()
            self.tickers = list(universe.keys())[: self.max_subscriptions]
            if not self.tickers:
                raise ValueError("Universe is empty")
            self._name_cache = universe
            logger.info(f"?뱤 Universe 濡쒕뱶 ?꾨즺: {len(self.tickers)}媛?醫낅ぉ")
            debug_tower.log("SYSTEM", "UNIVERSE_LOADED", {"count": len(self.tickers)})
        except Exception as e:
            logger.warning(f"?좑툘 Universe 濡쒕뱶 ?ㅽ뙣 ({e}), 湲곕낯 醫낅ぉ ?ъ슜")
            debug_tower.capture_snapshot("SYSTEM", e, "UNIVERSE_LOAD")
            self.tickers = list(self.DEFAULT_TICKERS)
            self._name_cache = {t: f"醫낅ぉ_{t}" for t in self.tickers}

        REGISTER_INTERVAL = 0.3
        RETRY_INTERVAL = 0.2
        RETRY_DELAY = 3.0

        self._subscribed_tickers.clear()
        failed_tickers: List[str] = []

        for idx, ticker in enumerate(self.tickers):
            try:
                try:
                    await self.kiwoom.register_realtime(ticker, self._handler, types=["0B"])
                    self._subscribed_tickers.append(ticker)
                    logger.debug(f"??{ticker} ?깅줉 ?깃났 ({idx+1}/{len(self.tickers)})")
                except Exception as e:
                    logger.warning(f"?좑툘 {ticker} ?깅줉 ?ㅽ뙣: {e}")
                    failed_tickers.append(ticker)

                await asyncio.sleep(REGISTER_INTERVAL)

            except Exception as e:
                logger.error(f"??{ticker} ?깅줉 以??ㅻ쪟: {e}")
                failed_tickers.append(ticker)

        logger.info(f"??1李??깅줉 ?꾨즺: ?깃났 {len(self._subscribed_tickers)}媛? ?ㅽ뙣 {len(failed_tickers)}媛?)

        if failed_tickers:
            logger.info(f"??{len(failed_tickers)}媛?醫낅ぉ 2李??щ벑濡??쒕룄 (3珥???...")
            await asyncio.sleep(RETRY_DELAY)

            retry_success = 0
            for ticker in failed_tickers:
                try:
                    await self.kiwoom.register_realtime(ticker, self._handler, types=["0B"])
                    self._subscribed_tickers.append(ticker)
                    retry_success += 1
                    logger.debug(f"??{ticker} ?щ벑濡??깃났")
                except Exception as e:
                    logger.warning(f"?좑툘 {ticker} ?щ벑濡??ㅽ뙣 (理쒖쥌): {e}")
                await asyncio.sleep(RETRY_INTERVAL)

            logger.info(
                f"??2李??щ벑濡??꾨즺: 異붽? ?깃났 {retry_success}媛? 理쒖쥌 ?ㅽ뙣 {len(failed_tickers) - retry_success}媛?
            )

        self._is_running = True
        self._last_scan_time = time.time()
        logger.info(f"??RealtimeMonitor ?쒖옉 ?꾨즺 (援щ룆 醫낅ぉ: {len(self._subscribed_tickers)}媛?")
        debug_tower.log("SYSTEM", "MONITOR_STARTED", {"count": len(self._subscribed_tickers)})

    def _on_data(self, data: Dict[str, Any]) -> None:
        # ?뵩 ?덉쇅 諛쒖깮 ??李몄“???덉쟾???대갚 蹂??(?먮낯? ticker 蹂?섎? 吏곸젒 李몄“?섏뿬
        # 留뚯빟 ?꾨옒 泥?以꾩뿉???덉쇅媛 ?섎㈃ UnboundLocalError媛 諛쒖깮???좎옱???꾪뿕???덉뿀??
        ticker_val: Any = data.get("ticker") or data.get("symbol") or data.get("item") or "UNKNOWN"
        try:
            ticker = data.get("ticker") or data.get("symbol") or data.get("item")
            if not ticker:
                return
            ticker = str(ticker)
            ticker_val = ticker

            data_type = data.get("type")
            parsed: Dict[str, Any] = {"ticker": ticker, "timestamp": data.get("timestamp", time.time()), "raw": data}

            if data_type == "0B" or "price" in data or "cur_prc" in data:
                raw_price = data.get("price") or data.get("cur_prc") or data.get("last")
                price: float = 0.0
                if raw_price is not None:
                    try:
                        price = float(raw_price)
                    except Exception:
                        price = 0.0
                raw_vol = data.get("volume") or data.get("acc_vol") or 0
                volume: int = 0
                try:
                    volume = int(raw_vol)
                except Exception:
                    volume = 0

                parsed["price"] = price
                parsed["volume"] = volume
                if ticker not in self._history:
                    self._history[ticker] = deque(maxlen=self._history_limit)
                self._history[ticker].append(parsed)

                debug_tower.log(ticker, "MONITOR_RECV", {"price": price, "volume": volume})

            elif data_type == "0A" or "buy_fpr_bid" in data or "sel_fpr_bid" in data:
                orderbook: Dict[str, List[Tuple[float, int]]] = {"bids": [], "asks": []}
                for i in range(1, 11):
                    if i == 1:
                        price_key, qty_key = "buy_fpr_bid", "buy_fpr_req"
                    else:
                        price_key, qty_key = f"buy_{i-1}th_pre_bid", f"buy_{i-1}th_pre_req"
                    p = data.get(price_key)
                    q = data.get(qty_key)
                    if p is not None and q is not None:
                        try:
                            orderbook["bids"].append((float(p), int(q)))
                        except Exception:
                            pass
                for i in range(1, 11):
                    if i == 1:
                        price_key, qty_key = "sel_fpr_bid", "sel_fpr_req"
                    else:
                        price_key, qty_key = f"sel_{i-1}th_pre_bid", f"sel_{i-1}th_pre_req"
                    p = data.get(price_key)
                    q = data.get(qty_key)
                    if p is not None and q is not None:
                        try:
                            orderbook["asks"].append((float(p), int(q)))
                        except Exception:
                            pass
                parsed["orderbook"] = orderbook
                if ticker not in self._orderbook_history:
                    self._orderbook_history[ticker] = deque(maxlen=self._orderbook_limit)
                self._orderbook_history[ticker].append(parsed)
                debug_tower.log(
                    ticker, "ORDERBOOK_RECV", {"bids": len(orderbook["bids"]), "asks": len(orderbook["asks"])}
                )

            else:
                parsed["raw_data"] = data

            if ticker in self._latest_data:
                self._latest_data[ticker].update(parsed)
            else:
                self._latest_data[ticker] = parsed

            try:
                self._message_queue.put_nowait(parsed)
            except asyncio.QueueFull:
                logger.warning(f"?좑툘 硫붿떆吏 ??媛??李????곗씠???쒕∼ ({ticker})")
                debug_tower.log(ticker, "QUEUE_FULL", {"queue_size": self._message_queue.qsize()})

        except Exception as e:
            logger.error(f"???곗씠???몃뱾留??ㅻ쪟: {e}", exc_info=True)
            debug_tower.capture_snapshot(str(ticker_val), e, "MONITOR_HANDLER")

    def _calculate_imbalance(self, bids: List[Tuple[float, int]], asks: List[Tuple[float, int]]) -> Tuple[float, str]:
        total_bid = sum(qty for _, qty in bids) if bids else 0
        total_ask = sum(qty for _, qty in asks) if asks else 0
        if total_bid + total_ask == 0:
            return 0.5, "?뽳툘 ?곗씠???놁쓬"
        imbalance = total_bid / (total_bid + total_ask)
        if imbalance > 0.65:
            pressure = f"?뵦 媛뺥븳 留ㅼ닔 ?뺣젰 ({imbalance:.1%})"
        elif imbalance < 0.35:
            pressure = f"?? 媛뺥븳 留ㅻ룄 ?뺣젰 ({imbalance:.1%})"
        else:
            pressure = f"?뽳툘 以묐┰ ({imbalance:.1%})"
        return imbalance, pressure

    async def scan(self) -> List[Dict[str, Any]]:
        if not self._is_running:
            return []

        detected: List[Dict[str, Any]] = []
        current_time = time.time()
        changed_tickers = [
            ticker for ticker, data in self._latest_data.items()
            if float(data.get("timestamp", 0)) > self._last_scan_time
        ]

        if not changed_tickers:
            return []

        regime = self._get_current_regime()

        for ticker in changed_tickers:
            data = self._latest_data.get(ticker, {})
            price: float = float(data.get("price", 0.0))
            if price <= 0:
                continue

            history = self._history.get(ticker, deque())
            if len(history) < 2:
                continue

            prev_data = history[-2]
            prev_price: float = float(prev_data.get("price", price))
            if prev_price <= 0:
                continue

            change_ratio = (price - prev_price) / prev_price

            orderbook: Dict[str, List[Tuple[float, int]]] = data.get("orderbook", {})
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])

            support_level: Optional[float] = None
            resistance_level: Optional[float] = None
            if bids:
                max_bid = max(bids, key=lambda x: x[1])
                support_level = max_bid[0]
            if asks:
                max_ask = max(asks, key=lambda x: x[1])
                resistance_level = max_ask[0]

            imbalance, pressure = self._calculate_imbalance(bids, asks)

            if abs(change_ratio) >= self.price_change_ratio:
                action = "BUY" if change_ratio > 0 else "SELL"
                positives = ["湲됰벑 媛먯?"] if change_ratio > 0 else ["湲됰씫 媛먯?"]
                insight = ""
                if support_level and price > support_level:
                    insight += f" | ?뱢 吏吏??{support_level:,.0f}???곹뼢 ?댄깉"
                if resistance_level and price < resistance_level:
                    insight += f" | ?뱣 ???꽑 {resistance_level:,.0f}???섑뼢 ?댄깉"

                last_time = self._last_signal_time.get(ticker, 0.0)
                last_action = self._last_signal_action.get(ticker, "")
                is_emergency = abs(change_ratio) > self.emergency_threshold

                if not is_emergency and last_action == action and (current_time - last_time) < self.cooldown_seconds:
                    continue

                self._last_signal_time[ticker] = current_time
                self._last_signal_action[ticker] = action

                stock_name = self._name_cache.get(ticker, ticker)

                score = min(0.95, 0.4 + abs(change_ratio) * 12.5)
                confidence = min(0.9, 0.5 + abs(change_ratio) * 5)

                detected.append(
                    {
                        "ticker": ticker,
                        "name": stock_name,
                        "price": price,
                        "entry_price": price,
                        "action": action,
                        "score": score,
                        "confidence": confidence,
                        "positives": positives + [f"蹂?숇쪧: {change_ratio:+.2%}{insight}"],
                        "negatives": ["?쒖옣 蹂?숈꽦 二쇱쓽"],
                        "timestamp": current_time,
                        "momentum": change_ratio,
                        "volume": int(data.get("volume", 0)),
                        "regime": regime,
                        "flow": {},
                        "support_level": support_level,
                        "resistance_level": resistance_level,
                        "imbalance": imbalance,
                        "pressure": pressure,
                    }
                )
                debug_tower.log(
                    ticker,
                    "SIGNAL_DETECTED",
                    {"action": action, "change": change_ratio, "score": score, "regime": regime},
                )

        self._last_scan_time = current_time
        return detected

    async def resubscribe_all(self) -> None:
        if not self._subscribed_tickers:
            return
        logger.info(f"?봽 ??λ맂 {len(self._subscribed_tickers)}媛?醫낅ぉ ?ш뎄???쒖옉...")
        debug_tower.log("SYSTEM", "RESUBSCRIBE_START", {"count": len(self._subscribed_tickers)})
        for ticker in self._subscribed_tickers:
            try:
                await self.kiwoom.register_realtime(ticker, self._handler, types=["0B"])
                await asyncio.sleep(0.15)
            except Exception as e:
                logger.error(f"???ш뎄???ㅽ뙣 ({ticker}): {e}")
                debug_tower.capture_snapshot(ticker, e, "RESUBSCRIBE")
        logger.info("???꾩껜 醫낅ぉ ?ш뎄???꾨즺")
        debug_tower.log("SYSTEM", "RESUBSCRIBE_COMPLETE", {})

    def get_latest_price(self, ticker: str) -> Optional[float]:
        data = self._latest_data.get(ticker)
        return float(data["price"]) if data and "price" in data else None

    def get_orderbook(self, ticker: str) -> Optional[Dict[str, Any]]:
        data = self._latest_data.get(ticker)
        return data.get("orderbook") if data else None

    def get_subscribed_count(self) -> int:
        return len(self._subscribed_tickers)

    def is_running(self) -> bool:
        return self._is_running

    async def stop(self) -> None:
        self._is_running = False
        for ticker in self._subscribed_tickers:
            try:
                await self.kiwoom.unregister_realtime(ticker)
            except Exception:
                pass
        self._subscribed_tickers.clear()
        self._latest_data.clear()
        self._history.clear()
        self._orderbook_history.clear()
        logger.info("?썞 RealtimeMonitor 以묒? ?꾨즺")
        debug_tower.log("SYSTEM", "MONITOR_STOPPED", {})
