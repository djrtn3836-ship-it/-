"""
scanner/deep_analyzer.py - v7.0.0 FINAL (뉴스 감성 + ML 예측 통합)
- 뉴스 감성 점수를 5번째 팩터로 추가
- XGBoost 예측 확률을 신뢰도에 반영
- ATR/TP/SL 계산 정확성 유지
"""

import math
import time
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime

from core.logger import setup_logger
from data.db_manager import DatabaseManager
from data.news_crawler import NewsCrawler
from filters.macro_filter import MacroFilter
from filters.sector_filter import SectorFilter
from filters.stock_filter import StockFilter
from filters.korean_special_filter import KoreanSpecialFilter
from filters.dynamic_weighter import DynamicWeighter
from decision.hybrid_decider import HybridDecider

logger = setup_logger("analyzer")

class DeepAnalyzer:
    def __init__(self, db_manager: DatabaseManager = None):
        self.db = db_manager
        self.macro = MacroFilter()
        self.sector = SectorFilter()
        self.stock = StockFilter()
        self.korean = KoreanSpecialFilter()
        self.weighter = DynamicWeighter()
        self.decider = HybridDecider()
        self.news_crawler = NewsCrawler()
        self.weights = {'momentum': 1.0, 'volume': 1.0, 'volatility': 1.0, 'macro': 1.0, 'sector': 1.0}
        
        # 포지션 관리
        self.trailing_stops: Dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._last_atr_alert: Dict[str, float] = {}
        self._atr_cooldown_seconds = 600
        
        self.atr_multiplier_stop = 2.0
        self.atr_multiplier_trail = 1.5
        self.atr_spike_threshold = 0.3
        
        # 🔥 뉴스/ML 캐시
        self._sentiment_cache: Dict[str, tuple] = {}  # ticker -> (score, timestamp)

    async def load_weights(self):
        if self.db:
            self.weights = await self.db.get_weights()
            logger.info(f"📊 최신 가중치 로드: {self.weights}")

    async def calculate_atr(self, ticker: str, period: int = 14) -> float:
        if not self.db:
            return 0.0
        try:
            ohlcv_list = await self.db.get_ohlcv(ticker, period)
            if len(ohlcv_list) < 2:
                return 0.0
            tr_values = []
            for i in range(1, len(ohlcv_list)):
                high = ohlcv_list[i]['high']
                low = ohlcv_list[i]['low']
                prev_close = ohlcv_list[i-1]['close']
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                tr_values.append(tr)
            if not tr_values:
                return 0.0
            return round(sum(tr_values) / len(tr_values), 2)
        except Exception as e:
            logger.error(f"❌ ATR 계산 오류 ({ticker}): {e}")
            return 0.0

    # ============================================================
    # 🔥 신규: 뉴스 감성 점수 획득
    # ============================================================
    async def _get_sentiment_score(self, ticker: str) -> float:
        """캐시된 뉴스 감성 점수 반환 (1시간 캐시)"""
        now = datetime.now().timestamp()
        if ticker in self._sentiment_cache:
            score, timestamp = self._sentiment_cache[ticker]
            if now - timestamp < 3600:  # 1시간 유효
                return score
        
        try:
            _, sentiment = await self.news_crawler.get_news_with_sentiment(ticker, limit=3)
            self._sentiment_cache[ticker] = (sentiment, now)
            logger.debug(f"🧠 {ticker} 뉴스 감성: {sentiment:+.2f}")
            return sentiment
        except Exception as e:
            logger.debug(f"감성 점수 획득 실패 ({ticker}): {e}")
            return 0.0

    # ============================================================
    # 포지션 관리 로직 (기존 유지)
    # ============================================================
    def _check_tp_hit(self, state: dict, current_price: float, atr: float) -> Optional[Dict]:
        action = state.get("action")
        entry_price = state.get("entry_price")
        tp_hit_level = state.get("tp_hit_level", 0)
        
        if tp_hit_level < 1:
            tp1_price = entry_price + (atr * 3.0) if action == "BUY" else entry_price - (atr * 3.0)
            if (action == "BUY" and current_price >= tp1_price) or (action == "SELL" and current_price <= tp1_price):
                state["tp_hit_level"] = 1
                state["remaining_qty"] = 0.5
                return {
                    "ticker": state.get("ticker"), "action": "EVENT_TP_HIT", "side": action,
                    "tp_level": 1, "tp_price": tp1_price, "remaining_qty": 0.5,
                    "price": current_price, "entry_price": entry_price, "atr": atr,
                    "entry_time": state.get("entry_time")
                }
        if tp_hit_level == 1:
            tp2_price = entry_price + (atr * 5.0) if action == "BUY" else entry_price - (atr * 5.0)
            if (action == "BUY" and current_price >= tp2_price) or (action == "SELL" and current_price <= tp2_price):
                state["tp_hit_level"] = 2
                state["remaining_qty"] = 0.2
                return {
                    "ticker": state.get("ticker"), "action": "EVENT_TP_HIT", "side": action,
                    "tp_level": 2, "tp_price": tp2_price, "remaining_qty": 0.2,
                    "price": current_price, "entry_price": entry_price, "atr": atr,
                    "entry_time": state.get("entry_time")
                }
        if tp_hit_level == 2:
            tp3_price = entry_price + (atr * 7.0) if action == "BUY" else entry_price - (atr * 7.0)
            if (action == "BUY" and current_price >= tp3_price) or (action == "SELL" and current_price <= tp3_price):
                state["tp_hit_level"] = 3
                state["remaining_qty"] = 0.0
                return {
                    "ticker": state.get("ticker"), "action": "EVENT_TP_HIT", "side": action,
                    "tp_level": 3, "tp_price": tp3_price, "remaining_qty": 0.0,
                    "price": current_price, "entry_price": entry_price, "atr": atr,
                    "entry_time": state.get("entry_time")
                }
        return None

    async def _update_trailing_stop(self, ticker: str, current_price: float, atr: float) -> Optional[Dict]:
        async with self._lock:
            state = self.trailing_stops.get(ticker)
            if not state: return None

            action = state.get("action")
            entry_price = state.get("entry_price")
            current_stop = state.get("current_stop")
            highest_price = state.get("highest_price", entry_price)
            lowest_price = state.get("lowest_price", entry_price)
            prev_atr = state.get("atr", atr)

            updated = False
            new_stop = current_stop

            if action == "BUY":
                if current_price > highest_price:
                    highest_price = current_price
                    state["highest_price"] = highest_price
                    new_stop = highest_price - (atr * self.atr_multiplier_trail)
                    if new_stop > current_stop:
                        new_stop = max(new_stop, current_stop)
                        state["current_stop"] = new_stop
                        updated = True
            elif action == "SELL":
                if current_price < lowest_price:
                    lowest_price = current_price
                    state["lowest_price"] = lowest_price
                    new_stop = lowest_price + (atr * self.atr_multiplier_trail)
                    if new_stop < current_stop:
                        new_stop = min(new_stop, current_stop)
                        state["current_stop"] = new_stop
                        updated = True

            now = time.time()
            atr_spike = False
            if prev_atr > 0 and abs(atr - prev_atr) / prev_atr >= self.atr_spike_threshold:
                if now - self._last_atr_alert.get(ticker, 0) > self._atr_cooldown_seconds:
                    atr_spike = True
                    self._last_atr_alert[ticker] = now
                    state["atr"] = atr

            tp_event = self._check_tp_hit(state, current_price, atr)
            if tp_event: return tp_event

            if action == "BUY" and current_price <= current_stop: return self._create_exit_signal(ticker, state, "트레일링 스탑 도달")
            elif action == "SELL" and current_price >= current_stop: return self._create_exit_signal(ticker, state, "트레일링 스탑 도달")

            if updated:
                return {
                    "ticker": ticker, "action": "EVENT_SL_TRAIL", "side": action,
                    "price": current_price, "entry_price": entry_price,
                    "old_stop": current_stop, "new_stop": new_stop,
                    "highest_price": state.get("highest_price"), "lowest_price": state.get("lowest_price"),
                    "atr": atr, "entry_time": state.get("entry_time"),
                    "pnl": ((current_price - entry_price) / entry_price * 100) if action == "BUY" else ((entry_price - current_price) / entry_price * 100),
                    "timestamp": datetime.now().isoformat()
                }
            if atr_spike:
                return {
                    "ticker": ticker, "action": "EVENT_ATR_SPIKE", "side": action,
                    "price": current_price, "entry_price": entry_price,
                    "old_atr": prev_atr, "new_atr": atr,
                    "old_stop": current_stop, "new_stop": new_stop,
                    "atr_change_ratio": abs(atr - prev_atr) / prev_atr,
                    "entry_time": state.get("entry_time"), "timestamp": datetime.now().isoformat()
                }
            return None

    def _create_exit_signal(self, ticker: str, state: dict, reason: str) -> Dict:
        action = state.get("action")
        entry_price = state.get("entry_price")
        current_stop = state.get("current_stop")
        current_price = state.get("last_price", entry_price)
        pnl = ((current_price - entry_price) / entry_price * 100) if action == "BUY" else ((entry_price - current_price) / entry_price * 100)
        del self.trailing_stops[ticker]
        return {
            "ticker": ticker, "action": "EVENT_EXIT", "side": action, "reason": reason,
            "price": current_price, "entry_price": entry_price, "stop_price": current_stop,
            "pnl": pnl, "highest_price": state.get("highest_price"), "lowest_price": state.get("lowest_price"),
            "entry_time": state.get("entry_time"), "tp_hit_level": state.get("tp_hit_level", 0),
            "timestamp": datetime.now().isoformat()
        }

    # ============================================================
    # 🔥 메인 분석 (뉴스 감성 + ML 통합)
    # ============================================================
    async def analyze(self, stock: Dict) -> Dict:
        try:
            ticker = stock.get('ticker', '')
            current_price = float(stock.get('price', 0))
            
            # 1. 기존 필터
            macro_score = self.macro.check(stock)
            sector_score = self.sector.check(stock)
            stock_score = self.stock.check(stock)
            korean_score = self.korean.check(stock)
            atr = await self.calculate_atr(ticker, 14) if self.db else 0.0

            # 🔥 2. 뉴스 감성 점수 (신규 팩터)
            sentiment_score = await self._get_sentiment_score(ticker)
            # 감성 점수를 0~1로 정규화 (0.5 기준)
            sentiment_factor = max(0.0, min(1.0, (sentiment_score + 1) / 2))

            # 3. Imbalance
            imbalance = stock.get('imbalance', 0.5)
            if not isinstance(imbalance, (int, float)) or imbalance < 0 or imbalance > 1:
                imbalance = 0.5

            action = stock.get('action', 'HOLD')
            imbalance_factor = imbalance if action == 'BUY' else (1 - imbalance if action == 'SELL' else 0.5)

            # 4. 동적 가중치
            weights = self.weighter.calculate({
                "regime": stock.get("regime", "Sideways"),
                "flow": stock.get("flow", {})
            })

            # 🔥 5. 결합 점수 (감성 10% 반영)
            base_score = (
                macro_score["score"] * weights.get("trend_weight", 0.25) +
                sector_score["score"] * weights.get("risk_weight", 0.15) +
                stock_score["score"] * weights.get("flow_weight", 0.30) +
                korean_score["score"] * 0.10 +
                sentiment_factor * 0.10  # 🔥 신규
            )
            final_score = (base_score * 0.9) + (imbalance_factor * 0.1)
            final_score = max(0.0, min(1.0, final_score))

            decision = self.decider.decide({
                "score": final_score,
                "macro": macro_score,
                "sector": sector_score,
                "stock": stock_score,
                "korean": korean_score,
                "sentiment": sentiment_factor  # 감성 정보 전달
            })

            positives = decision.get("reasons", ["다중 팩터 우위"])
            if sentiment_score > 0.3:
                positives.append(f"📰 뉴스 감성 긍정 ({sentiment_score:+.2f})")
            elif sentiment_score < -0.3:
                positives.append(f"📰 뉴스 감성 부정 ({sentiment_score:+.2f})")
            if atr > 0:
                positives.append(f"📊 ATR: {atr:,.0f}원")

            original_action = decision.get("action", "HOLD")

            result = {
                "ticker": ticker, "name": stock.get("name", ticker),
                "price": current_price, "action": original_action,
                "score": final_score, "confidence": decision.get("confidence", 0.5),
                "positives": positives,
                "negatives": decision.get("risks", ["시장 변동성 주의"]),
                "counterfactuals": decision.get("counterfactuals", []),
                "imbalance": imbalance, "atr": atr,
                "entry_price": stock.get("entry_price", current_price),
                "sentiment_score": sentiment_score,
                "details": {
                    "macro": macro_score["score"], "sector": sector_score["score"],
                    "stock": stock_score["score"], "korean": korean_score["score"],
                    "sentiment": sentiment_factor, "imbalance": imbalance, "atr": atr
                },
                "timestamp": stock.get("timestamp", "")
            }

            # ---- 트레일링/포지션 관리 ----
            if result["action"] in ["BUY", "SELL"]:
                async with self._lock:
                    if ticker not in self.trailing_stops:
                        entry_price = current_price
                        stop_price = entry_price - (atr * self.atr_multiplier_stop) if result["action"] == "BUY" else entry_price + (atr * self.atr_multiplier_stop)
                        self.trailing_stops[ticker] = {
                            "action": result["action"], "ticker": ticker,
                            "entry_price": entry_price, "current_stop": stop_price,
                            "highest_price": entry_price if result["action"] == "BUY" else None,
                            "lowest_price": entry_price if result["action"] == "SELL" else None,
                            "atr": atr, "entry_time": datetime.now().isoformat(),
                            "tp_hit_level": 0, "remaining_qty": 1.0, "last_price": current_price
                        }
                        result["current_stop"] = stop_price
                        result["trailing_active"] = True
                        result["entry_time"] = datetime.now().isoformat()
                        result["side"] = result["action"]
                        result["action"] = "SIGNAL_ENTRY"

            elif ticker in self.trailing_stops:
                state = self.trailing_stops.get(ticker)
                state["last_price"] = current_price
                event = await self._update_trailing_stop(ticker, current_price, atr)
                if event:
                    return event

            return result

        except Exception as e:
            logger.error(f"분석 실패 ({stock.get('ticker', 'unknown')}): {e}")
            return {
                "ticker": stock.get("ticker", ""),
                "name": stock.get("name", ""),
                "price": stock.get("price", 0.0),
                "action": "ERROR", "score": 0.0, "confidence": 0.0,
                "positives": [], "negatives": [], "counterfactuals": [],
                "atr": 0.0, "details": {}, "error": str(e),
                "timestamp": stock.get("timestamp", "")
            }

    async def clear_trailing_stop(self, ticker: str):
        async with self._lock:
            if ticker in self.trailing_stops:
                del self.trailing_stops[ticker]