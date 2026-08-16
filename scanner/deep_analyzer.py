"""
scanner/deep_analyzer.py - v7.2.2 FINAL (신규 진입 수정 + 락 보완 + 감성 통합)
- HybridDecider의 영문 액션(BUY/SELL) 수용
- trailing_stops 락 누락 구간 보완
- 뉴스 감성 분석 통합 (CONTEXT.md 명세 반영)
- 분석 성능 최적화 (매 틱 DB 저장 완화)
"""

import math
import time
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

from core.logger import setup_logger
from data.db_manager import DatabaseManager
from data.news_crawler import NewsCrawler  # 🔥 감성 분석용
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
        self.weights = {'momentum': 1.0, 'volume': 1.0, 'volatility': 1.0, 'macro': 1.0, 'sector': 1.0}
        
        self.trailing_stops: Dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._last_atr_alert: Dict[str, float] = {}
        self._atr_cooldown_seconds = 600
        
        # 🔥 OHLCV 캐시
        self._ohlcv_cache: Dict[str, Dict] = {}
        self._cache_time: Dict[str, float] = {}
        
        # 🔥 피드백 통계 캐시
        self._feedback_stats = {"win_rate": 0.5, "sharpe": 1.0, "sample_count": 0, "avg_return": 0.0}
        self._feedback_stats_loaded = False
        
        # 🔥 뉴스 크롤러 (지연 초기화)
        self._news_crawler = None
        
        # 설정
        self.max_hold_hours = 2.0
        self.time_warning_minutes = 30
        self.sideways_threshold_minutes = 30
        self.sideways_price_ratio = 0.3
        self.trail_aggressive_threshold = 5.0
        self.atr_multiplier_stop = 2.0
        self.atr_multiplier_trail = 1.5
        self.atr_spike_threshold = 0.3

    async def load_weights(self):
        if self.db:
            self.weights = await self.db.get_weights()
            self._feedback_stats = await self.db.get_feedback_stats(days=30)
            self._feedback_stats_loaded = True
            logger.info(f"📊 최신 가중치 로드: {self.weights}")
            logger.info(f"📊 피드백 통계: 승률 {self._feedback_stats['win_rate']:.1%}, 샤프 {self._feedback_stats['sharpe']:.2f}")

    async def calculate_atr(self, ticker: str, period: int = 14) -> float:
        if not self.db:
            return 0.0
        try:
            ohlcv_list = await self.db.get_ohlcv(ticker, period)
            # 🔥 방어: high==0 or low==0인 오염 레코드 제거
            clean_list = [d for d in ohlcv_list if d.get('high', 0) > 0 and d.get('low', 0) > 0]
            if len(clean_list) < 2:
                return 0.0
            tr_values = []
            for i in range(1, len(clean_list)):
                high = clean_list[i]['high']
                low = clean_list[i]['low']
                prev_close = clean_list[i-1]['close']
                tr1 = high - low
                tr2 = abs(high - prev_close)
                tr3 = abs(low - prev_close)
                tr = max(tr1, tr2, tr3)
                tr_values.append(tr)
            if not tr_values:
                return 0.0
            atr = sum(tr_values) / len(tr_values)
            return round(atr, 2)
        except Exception as e:
            logger.error(f"❌ {ticker} ATR 계산 오류: {e}")
            return 0.0

    # ============================================================
    # 🔥 뉴스 감성 분석 (지연 초기화)
    # ============================================================
    async def _get_sentiment_score(self, ticker: str) -> float:
        """뉴스 감성 점수 조회 (캐시 포함)"""
        try:
            if self._news_crawler is None:
                self._news_crawler = NewsCrawler()
            _, sentiment = await self._news_crawler.get_news_with_sentiment(
                ticker, limit=5, cache_seconds=3600
            )
            return float(sentiment) if sentiment is not None else 0.0
        except Exception as e:
            logger.debug(f"감성 점수 조회 실패 ({ticker}): {e}")
            return 0.0

    # ============================================================
    # 🔥 OHLCV 캐시 + 기술 지표
    # ============================================================
    async def _get_cached_ohlcv(self, ticker: str, period: int = 30) -> Dict:
        now = time.time()
        cache_key = f"{ticker}_{period}"
        if cache_key in self._ohlcv_cache and (now - self._cache_time.get(cache_key, 0)) < 60:
            return self._ohlcv_cache[cache_key]
        
        if not self.db:
            return {}
        
        data = await self.db.get_ohlcv(ticker, period)
        if len(data) < 5:
            return {}
        
        closes = [d['close'] for d in data]
        highs = [d['high'] for d in data]
        lows = [d['low'] for d in data]
        volumes = [d.get('volume', 0) for d in data if d.get('volume', 0) > 0]
        
        def ema(values, n):
            if len(values) < n:
                return values[-1] if values else 0
            k = 2 / (n + 1)
            ema_val = values[0]
            for v in values[1:]:
                ema_val = v * k + ema_val * (1 - k)
            return ema_val
        
        def rsi(values, n=14):
            if len(values) < n + 1:
                return 50
            gains, losses = [], []
            for i in range(1, len(values)):
                diff = values[i] - values[i-1]
                if diff > 0:
                    gains.append(diff); losses.append(0)
                else:
                    gains.append(0); losses.append(-diff)
            avg_gain = sum(gains[-n:]) / n
            avg_loss = sum(losses[-n:]) / n
            if avg_loss == 0:
                return 100
            rs = avg_gain / avg_loss
            return 100 - (100 / (1 + rs))
        
        avg_volume = sum(volumes) / len(volumes) if volumes else 1
        current_volume = volumes[-1] if volumes else 1
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
        
        result = {
            "current_price": data[-1]['close'],
            "high": data[-1]['high'],
            "low": data[-1]['low'],
            "volume_ratio": round(volume_ratio, 2),
            "ema5": round(ema(closes, 5), 2),
            "ema20": round(ema(closes, 20), 2),
            "ema60": round(ema(closes, 60), 2) if len(closes) >= 60 else round(ema(closes, len(closes)), 2),
            "rsi": round(rsi(closes, 14), 2),
        }
        
        self._ohlcv_cache[cache_key] = result
        self._cache_time[cache_key] = now
        return result

    # ============================================================
    # 🔥 4대 개체 평가 (기존 유지 + 감성 반영)
    # ============================================================
    def _evaluate_technical(self, ticker: str, entry_price: float, tech_data: Dict, atr: float) -> Dict:
        score = 0.0
        reasons = []
        if not tech_data:
            return {"score": 0.0, "reasons": ["📊 기술 데이터 부족"]}
        
        price = tech_data.get("current_price", entry_price)
        ema5 = tech_data.get("ema5", price)
        ema20 = tech_data.get("ema20", price)
        ema60 = tech_data.get("ema60", price)
        rsi = tech_data.get("rsi", 50)
        volume_ratio = tech_data.get("volume_ratio", 1.0)
        
        if ema5 > ema20 and ema20 > ema60:
            score += 0.4; reasons.append("📈 EMA 정배열 (+0.4)")
        elif ema5 > ema20:
            score += 0.2; reasons.append("📈 단기 상승 추세 (+0.2)")
        elif ema5 < ema20 and ema20 < ema60:
            score -= 0.4; reasons.append("📉 EMA 역배열 (-0.4)")
        elif ema5 < ema20:
            score -= 0.2; reasons.append("📉 단기 하락 추세 (-0.2)")
        
        if rsi > 80:
            score -= 0.7; reasons.append(f"🔥 과매수 (RSI {rsi:.0f}) (-0.7)")
        elif rsi > 70:
            score -= 0.4; reasons.append(f"⚠️ 과매수 임박 (RSI {rsi:.0f}) (-0.4)")
        elif rsi < 20:
            score += 0.7; reasons.append(f"📉 과매도 (RSI {rsi:.0f}) (+0.7)")
        elif rsi < 30:
            score += 0.4; reasons.append(f"📉 과매도 임박 (RSI {rsi:.0f}) (+0.4)")
        
        if volume_ratio > 2.0:
            score += 0.3; reasons.append(f"📊 거래량 급증 (×{volume_ratio:.1f}) (+0.3)")
        elif volume_ratio < 0.5:
            score -= 0.2; reasons.append(f"📊 거래량 부진 (×{volume_ratio:.1f}) (-0.2)")
        
        if atr > 0:
            move_ratio = abs(price - entry_price) / atr
            if move_ratio > 2.0:
                score += 0.3; reasons.append(f"💪 강한 모멘텀 (ATR×{move_ratio:.1f}) (+0.3)")
        
        return {"score": max(-1.0, min(1.0, score)), "reasons": reasons[:3]}

    def _evaluate_risk(self, ticker: str, current_price: float, stop_price: float, atr: float, highest_price: float, feedback_stats: Dict) -> Dict:
        score = 0.0
        reasons = []
        win_rate = feedback_stats.get("win_rate", 0.5)
        sharpe = feedback_stats.get("sharpe", 1.0)
        
        risk_penalty = 1.2 if win_rate < 0.4 else (0.8 if win_rate > 0.6 else 1.0)
        
        if stop_price > 0:
            distance_to_stop = (current_price - stop_price) / current_price * 100
            if distance_to_stop < 0.5:
                score -= 0.9 * risk_penalty
                reasons.append(f"🚨 손절가 임박 ({distance_to_stop:.1f}%)")
            elif distance_to_stop < 1.0:
                score -= 0.5 * risk_penalty
                reasons.append(f"⚠️ 손절가 근접 ({distance_to_stop:.1f}%)")
            elif distance_to_stop < 2.0:
                score -= 0.2 * risk_penalty
                reasons.append(f"🔻 손절가 접근 ({distance_to_stop:.1f}%)")
        
        if highest_price > current_price:
            drawdown = (highest_price - current_price) / highest_price * 100
            if drawdown > 5.0:
                score -= 0.5 * risk_penalty
                reasons.append(f"📉 급락 감지 ({drawdown:.1f}%)")
            elif drawdown > 3.0:
                score -= 0.2 * risk_penalty
                reasons.append(f"📉 조정 발생 ({drawdown:.1f}%)")
        
        if sharpe > 1.5 and win_rate > 0.55:
            score += 0.2
            reasons.append(f"✅ 과거 성과 우수 (샤프 {sharpe:.2f}) (+0.2)")
        elif sharpe < 0.5 and win_rate < 0.45:
            score -= 0.3
            reasons.append(f"⚠️ 과거 성과 부진 (샤프 {sharpe:.2f}) (-0.3)")
        
        return {"score": max(-1.0, min(1.0, score)), "reasons": reasons[:3]}

    def _evaluate_time_value(self, entry_time: str, current_price: float, entry_price: float) -> Dict:
        score = 0.0
        reasons = []
        try:
            entry_dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
            now = datetime.now()
            elapsed_hours = (now - entry_dt).total_seconds() / 3600
        except:
            elapsed_hours = 0
        
        if elapsed_hours < 0.01:
            return {"score": 0.0, "reasons": ["⏳ 진입 직전"]}
        
        pnl_pct = ((current_price - entry_price) / entry_price) * 100
        annualized_return = (pnl_pct / elapsed_hours) * 24 * 365 if elapsed_hours > 0 else 0
        
        if annualized_return > 30:
            score += 0.7; reasons.append(f"💰 연환산 수익률 {annualized_return:.0f}% (+0.7)")
        elif annualized_return > 15:
            score += 0.4; reasons.append(f"💰 연환산 수익률 {annualized_return:.0f}% (+0.4)")
        elif annualized_return < -30:
            score -= 0.7; reasons.append(f"📉 연환산 손실률 {annualized_return:.0f}% (-0.7)")
        
        if elapsed_hours > 1 and pnl_pct < 1.0:
            score -= 0.3
            reasons.append(f"⏳ 시간 대비 수익률 부진 ({elapsed_hours:.1f}시간) (-0.3)")
        
        return {"score": max(-1.0, min(1.0, score)), "reasons": reasons[:3]}

    def _evaluate_microstructure(self, ticker: str, tech_data: Dict, imbalance: float) -> Dict:
        score = 0.0
        reasons = []
        volume_ratio = tech_data.get("volume_ratio", 1.0) if tech_data else 1.0
        
        if imbalance > 0.6:
            score += 0.3; reasons.append(f"⚖️ 매수 우세 ({imbalance:.0%}) (+0.3)")
        elif imbalance < 0.4:
            score -= 0.3; reasons.append(f"⚖️ 매도 우세 ({imbalance:.0%}) (-0.3)")
        else:
            reasons.append(f"⚖️ 중립 ({imbalance:.0%}) (0.0)")
        
        if volume_ratio > 1.5:
            score += 0.3; reasons.append(f"📊 거래량 활발 (×{volume_ratio:.1f}) (+0.3)")
        elif volume_ratio < 0.6:
            score -= 0.2; reasons.append(f"📊 거래량 부진 (×{volume_ratio:.1f}) (-0.2)")
        
        return {"score": max(-1.0, min(1.0, score)), "reasons": reasons[:3]}

    # ============================================================
    # 🔥 합의 엔진
    # ============================================================
    def _run_consensus(self, state: dict, current_price: float, atr: float, tech_data: Dict, imbalance: float) -> Dict:
        ticker = state.get("ticker")
        entry_price = state.get("entry_price")
        stop_price = state.get("current_stop")
        entry_time = state.get("entry_time")
        highest_price = state.get("highest_price", entry_price)
        
        tech = self._evaluate_technical(ticker, entry_price, tech_data, atr)
        risk = self._evaluate_risk(ticker, current_price, stop_price, atr, highest_price, self._feedback_stats)
        time_val = self._evaluate_time_value(entry_time, current_price, entry_price)
        micro = self._evaluate_microstructure(ticker, tech_data, imbalance)
        
        base_risk_weight = 2.0 if self._feedback_stats.get("win_rate", 0.5) < 0.4 else 1.8
        weights = {"technical": 1.0, "risk": base_risk_weight, "time_value": 0.8, "micro": 0.6}
        
        weighted_sum = (
            tech["score"] * weights["technical"] +
            risk["score"] * weights["risk"] +
            time_val["score"] * weights["time_value"] +
            micro["score"] * weights["micro"]
        )
        total_weight = sum(weights.values())
        consensus_score = max(-1.0, min(1.0, weighted_sum / total_weight))
        
        votes = {"technical": tech["score"], "risk": risk["score"], "time_value": time_val["score"], "micro": micro["score"]}
        hold_votes = sum(1 for v in votes.values() if v > 0.2)
        exit_votes = sum(1 for v in votes.values() if v < -0.2)
        
        if consensus_score > 0.4 and hold_votes >= 3:
            recommendation, action_label = "HOLD", "보유 유지"
        elif consensus_score < -0.4 and exit_votes >= 2:
            recommendation, action_label = "EXIT", "청산 권고"
        else:
            recommendation, action_label = "HOLD", "관망 (추가 데이터 필요)"
        
        all_reasons = (tech["reasons"] + risk["reasons"] + time_val["reasons"] + micro["reasons"])[:5]
        summary = f"합의: {consensus_score:.2f} | 기술:{tech['score']:.2f} 리스크:{risk['score']:.2f} 시간:{time_val['score']:.2f} 수급:{micro['score']:.2f}"
        
        return {
            "recommendation": recommendation,
            "action_label": action_label,
            "consensus_score": consensus_score,
            "votes": votes,
            "reasons": all_reasons,
            "summary": summary
        }

    # ============================================================
    # 🔥 TP 도달 체크
    # ============================================================
    def _check_tp_hit(self, state: dict, current_price: float) -> Optional[Dict]:
        action = state.get("action")
        entry_price = state.get("entry_price")
        tp_hit_level = state.get("tp_hit_level", 0)
        tp1 = state.get("tp1_price", entry_price + 0)
        tp2 = state.get("tp2_price", entry_price + 0)
        tp3 = state.get("tp3_price", entry_price + 0)
        
        if tp_hit_level < 1:
            if (action == "BUY" and current_price >= tp1) or (action == "SELL" and current_price <= tp1):
                state["tp_hit_level"] = 1
                state["remaining_qty"] = 0.5
                return {
                    "ticker": state.get("ticker"),
                    "action": "EVENT_TP_HIT",
                    "side": action,
                    "tp_level": 1,
                    "tp_price": tp1,
                    "remaining_qty": 0.5,
                    "price": current_price,
                    "entry_price": entry_price,
                    "entry_time": state.get("entry_time"),
                    "recommendation": "PARTIAL_EXIT",
                    "recommendation_reason": f"TP1 도달 → 50% 청산"
                }
        if tp_hit_level == 1:
            if (action == "BUY" and current_price >= tp2) or (action == "SELL" and current_price <= tp2):
                state["tp_hit_level"] = 2
                state["remaining_qty"] = 0.2
                return {
                    "ticker": state.get("ticker"),
                    "action": "EVENT_TP_HIT",
                    "side": action,
                    "tp_level": 2,
                    "tp_price": tp2,
                    "remaining_qty": 0.2,
                    "price": current_price,
                    "entry_price": entry_price,
                    "entry_time": state.get("entry_time"),
                    "recommendation": "PARTIAL_EXIT",
                    "recommendation_reason": f"TP2 도달 → 추가 30% 청산"
                }
        if tp_hit_level == 2:
            if (action == "BUY" and current_price >= tp3) or (action == "SELL" and current_price <= tp3):
                state["tp_hit_level"] = 3
                state["remaining_qty"] = 0.0
                return {
                    "ticker": state.get("ticker"),
                    "action": "EVENT_TP_HIT",
                    "side": action,
                    "tp_level": 3,
                    "tp_price": tp3,
                    "remaining_qty": 0.0,
                    "price": current_price,
                    "entry_price": entry_price,
                    "entry_time": state.get("entry_time"),
                    "recommendation": "EXIT",
                    "recommendation_reason": f"TP3 도달 → 전량 청산"
                }
        return None

    # ============================================================
    # 🔥 트레일링 스탑 업데이트
    # ============================================================
    async def _update_trailing_stop(self, ticker: str, current_price: float, atr: float, tech_data: Dict, imbalance: float) -> Optional[Dict]:
        # tech_data가 비어있으면 캐시에서 재조회 (Critical-03 해결)
        if not tech_data and self.db:
            tech_data = await self._get_cached_ohlcv(ticker, 30)
        
        async with self._lock:
            state = self.trailing_stops.get(ticker)
            if not state:
                return None

            action = state.get("action")
            entry_price = state.get("entry_price")
            current_stop = state.get("current_stop")
            highest_price = state.get("highest_price", entry_price)
            lowest_price = state.get("lowest_price", entry_price)
            prev_atr = state.get("atr", atr)
            
            state["last_update_time"] = datetime.now().isoformat()

            updated = False
            new_stop = current_stop

            if action == "BUY":
                if current_price > highest_price:
                    highest_price = current_price
                    state["highest_price"] = highest_price
                    pnl_pct = ((current_price - entry_price) / entry_price) * 100
                    trail_mult = 1.0 if pnl_pct > self.trail_aggressive_threshold else self.atr_multiplier_trail
                    new_stop = highest_price - (atr * trail_mult)
                    if new_stop > current_stop:
                        new_stop = max(new_stop, current_stop)
                        state["current_stop"] = new_stop
                        updated = True
            elif action == "SELL":
                if current_price < lowest_price:
                    lowest_price = current_price
                    state["lowest_price"] = lowest_price
                    pnl_pct = ((entry_price - current_price) / entry_price) * 100
                    trail_mult = 1.0 if pnl_pct > self.trail_aggressive_threshold else self.atr_multiplier_trail
                    new_stop = lowest_price + (atr * trail_mult)
                    if new_stop < current_stop:
                        new_stop = min(new_stop, current_stop)
                        state["current_stop"] = new_stop
                        updated = True

            now = time.time()
            atr_spike = False
            tp_adjusted = False
            if prev_atr > 0 and abs(atr - prev_atr) / prev_atr >= self.atr_spike_threshold:
                last_alert = self._last_atr_alert.get(ticker, 0)
                if now - last_alert > self._atr_cooldown_seconds:
                    atr_spike = True
                    self._last_atr_alert[ticker] = now
                    state["atr"] = atr
                    
                    if state.get("tp_hit_level", 0) < 2 and entry_price > 0:
                        old_tp2 = state.get("tp2_price", entry_price + (atr * 5.0))
                        old_tp3 = state.get("tp3_price", entry_price + (atr * 7.0))
                        if action == "BUY":
                            state["tp2_price"] = entry_price + (atr * 5.0)
                            state["tp3_price"] = entry_price + (atr * 7.0)
                        else:
                            state["tp2_price"] = entry_price - (atr * 5.0)
                            state["tp3_price"] = entry_price - (atr * 7.0)
                        tp_adjusted = True
                        logger.info(f"🔄 {ticker} 동적 TP 조정: TP2 {old_tp2:.0f}→{state['tp2_price']:.0f}, TP3 {old_tp3:.0f}→{state['tp3_price']:.0f}")

            tp_event = self._check_tp_hit(state, current_price)
            if tp_event:
                return tp_event

            if action == "BUY" and current_price <= current_stop:
                return self._create_exit_signal(ticker, state, "트레일링 스탑 도달")
            elif action == "SELL" and current_price >= current_stop:
                return self._create_exit_signal(ticker, state, "트레일링 스탑 도달")

            consensus = self._run_consensus(state, current_price, atr, tech_data, imbalance)
            
            if updated:
                return {
                    "ticker": ticker,
                    "action": "EVENT_SL_TRAIL",
                    "side": action,
                    "price": current_price,
                    "entry_price": entry_price,
                    "old_stop": current_stop,
                    "new_stop": new_stop,
                    "highest_price": state.get("highest_price"),
                    "lowest_price": state.get("lowest_price"),
                    "atr": atr,
                    "entry_time": state.get("entry_time"),
                    "pnl": ((current_price - entry_price) / entry_price * 100) if action == "BUY" else ((entry_price - current_price) / entry_price * 100),
                    "timestamp": datetime.now().isoformat(),
                    "consensus": consensus,
                    "tp_adjusted": tp_adjusted,
                    "tp2_price": state.get("tp2_price") or 0.0,
                    "tp3_price": state.get("tp3_price") or 0.0,
                }
            
            if atr_spike:
                return {
                    "ticker": ticker,
                    "action": "EVENT_ATR_SPIKE",
                    "side": action,
                    "price": current_price,
                    "entry_price": entry_price,
                    "old_atr": prev_atr,
                    "new_atr": atr,
                    "old_stop": current_stop,
                    "new_stop": new_stop,
                    "atr_change_ratio": abs(atr - prev_atr) / prev_atr,
                    "entry_time": state.get("entry_time"),
                    "timestamp": datetime.now().isoformat(),
                    "consensus": consensus,
                    "tp_adjusted": tp_adjusted,
                    "tp2_price": state.get("tp2_price") or 0.0,
                    "tp3_price": state.get("tp3_price") or 0.0,
                }
            
            if consensus.get("recommendation") == "EXIT" and consensus.get("consensus_score", 0) < -0.4:
                return {
                    "ticker": ticker,
                    "action": "EVENT_LIFECYCLE_ADVICE",
                    "side": action,
                    "price": current_price,
                    "entry_price": entry_price,
                    "entry_time": state.get("entry_time"),
                    "pnl": ((current_price - entry_price) / entry_price * 100) if action == "BUY" else ((entry_price - current_price) / entry_price * 100),
                    "advice": consensus,
                    "timestamp": datetime.now().isoformat(),
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
            "ticker": ticker,
            "action": "EVENT_EXIT",
            "side": action,
            "reason": reason,
            "price": current_price,
            "entry_price": entry_price,
            "stop_price": current_stop,
            "pnl": pnl,
            "highest_price": state.get("highest_price"),
            "lowest_price": state.get("lowest_price"),
            "entry_time": state.get("entry_time"),
            "tp_hit_level": state.get("tp_hit_level", 0),
            "timestamp": datetime.now().isoformat(),
        }

    # ============================================================
    # 🔥 메인 분석 (신규 진입 수정)
    # ============================================================
    async def analyze(self, stock: Dict) -> Dict:
        try:
            ticker = stock.get('ticker', '')
            current_price = float(stock.get('price', 0))
            imbalance = stock.get('imbalance', 0.5)
            if not isinstance(imbalance, (int, float)) or imbalance < 0 or imbalance > 1:
                imbalance = 0.5
            
            tech_data = await self._get_cached_ohlcv(ticker, 30) if self.db else {}
            atr = await self.calculate_atr(ticker, 14) if self.db else 0.0
            
            macro_score = self.macro.check(stock)
            sector_score = self.sector.check(stock)
            stock_score = self.stock.check(stock)
            korean_score = self.korean.check(stock)

            action = stock.get('action', 'HOLD')
            imbalance_factor = imbalance if action == 'BUY' else (1 - imbalance if action == 'SELL' else 0.5)

            weights = self.weighter.calculate({
                "regime": stock.get("regime", "Sideways"),
                "flow": stock.get("flow", {})
            })

            base_score = (
                macro_score["score"] * weights.get("trend_weight", 0.3) +
                sector_score["score"] * weights.get("risk_weight", 0.2) +
                stock_score["score"] * weights.get("flow_weight", 0.4) +
                korean_score["score"] * 0.1
            )
            
            # 🔥 뉴스 감성 점수 (10% 반영)
            sentiment_score = await self._get_sentiment_score(ticker)
            sentiment_factor = max(0.0, min(1.0, (sentiment_score + 1) / 2))
            
            final_score = (base_score * 0.85) + (imbalance_factor * 0.10) + (sentiment_factor * 0.05)
            final_score = max(0.0, min(1.0, final_score))

            # 🔥 HybridDecider 호출 (영문 액션 반환)
            decision = self.decider.decide({
                "score": final_score,
                "macro": macro_score,
                "sector": sector_score,
                "stock": stock_score,
                "korean": korean_score
            })

            # 🔥 decision["action"]은 "BUY"/"SELL"/"HOLD" 중 하나 (영문)
            # decision["action_label"]은 "강력 매수" 등 (한국어 표시용)
            original_action = decision.get("action", "HOLD")
            
            positives = decision.get("reasons", ["다중 팩터 우위"])
            positives.append(f"📊 ATR: {atr:,.0f}원" if atr > 0 else "📊 ATR: 수집 중")
            if tech_data:
                positives.append(f"📊 거래량 비율: {tech_data.get('volume_ratio', 1.0):.1f}x")
                positives.append(f"📈 RSI: {tech_data.get('rsi', 50):.0f}")
            if sentiment_score != 0:
                positives.append(f"📰 뉴스 감성: {sentiment_score:+.2f}")

            result = {
                "ticker": ticker,
                "name": stock.get("name", stock.get("ticker", "")),
                "price": current_price,
                "action": original_action,  # 🔥 영문 액션 (BUY/SELL/HOLD)
                "action_label": decision.get("action_label", ""),  # 한국어 표시
                "score": final_score,
                "confidence": decision.get("confidence", 0.5),
                "positives": positives,
                "negatives": decision.get("risks", ["시장 변동성 주의"]),
                "counterfactuals": decision.get("counterfactuals", []),
                "imbalance": imbalance,
                "atr": atr,
                "entry_price": stock.get("entry_price", current_price),
                "sentiment_score": sentiment_score,
                "details": {
                    "macro": macro_score["score"],
                    "sector": sector_score["score"],
                    "stock": stock_score["score"],
                    "korean": korean_score["score"],
                    "imbalance": imbalance,
                    "atr": atr,
                    "sentiment": sentiment_factor,
                },
                "timestamp": stock.get("timestamp", "")
            }

            # ---- 🔥 신규 포지션 등록 (영문 액션 체크) ----
            if result["action"] in ["BUY", "SELL"]:
                async with self._lock:
                    if ticker not in self.trailing_stops:
                        entry_price = current_price
                        tp1 = entry_price + (atr * 3.0) if result["action"] == "BUY" else entry_price - (atr * 3.0)
                        tp2 = entry_price + (atr * 5.0) if result["action"] == "BUY" else entry_price - (atr * 5.0)
                        tp3 = entry_price + (atr * 7.0) if result["action"] == "BUY" else entry_price - (atr * 7.0)
                        stop_price = entry_price - (atr * self.atr_multiplier_stop) if result["action"] == "BUY" else entry_price + (atr * self.atr_multiplier_stop)
                        
                        self.trailing_stops[ticker] = {
                            "action": result["action"],
                            "ticker": ticker,
                            "entry_price": entry_price,
                            "current_stop": stop_price,
                            "tp1_price": tp1,
                            "tp2_price": tp2,
                            "tp3_price": tp3,
                            "highest_price": entry_price if result["action"] == "BUY" else None,
                            "lowest_price": entry_price if result["action"] == "SELL" else None,
                            "atr": atr,
                            "entry_time": datetime.now().isoformat(),
                            "last_update_time": datetime.now().isoformat(),
                            "last_advice_time": 0,
                            "tp_hit_level": 0,
                            "remaining_qty": 1.0,
                            "last_price": current_price,
                        }
                        logger.info(f"✅ {ticker} 포지션 추적 시작 (액션: {result['action']}, TP1:{tp1:.0f}, TP2:{tp2:.0f}, TP3:{tp3:.0f})")
                        result["current_stop"] = stop_price
                        result["trailing_active"] = True
                        result["entry_time"] = datetime.now().isoformat()
                        result["side"] = result["action"]
                        result["action"] = "SIGNAL_ENTRY"  # 🔥 Telegram 이벤트 타입으로 변경
                        result["max_hold_hours"] = self.max_hold_hours
                        result["tp1"] = tp1
                        result["tp2"] = tp2
                        result["tp3"] = tp3

            elif ticker in self.trailing_stops:
                # 🔥 락 누락 구간 보완 (Critical-02 해결)
                async with self._lock:
                    state = self.trailing_stops.get(ticker)
                    if state:
                        state["last_price"] = current_price
                event = await self._update_trailing_stop(
                    ticker, current_price, atr, tech_data, imbalance
                )
                if event:
                    return event

            return result

        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return {
                "ticker": stock.get("ticker", ""),
                "name": stock.get("name", ""),
                "price": stock.get("price", 0.0),
                "action": "ERROR",
                "score": 0.0,
                "confidence": 0.0,
                "positives": [],
                "negatives": [],
                "counterfactuals": [],
                "atr": 0.0,
                "details": {},
                "error": str(e),
                "timestamp": stock.get("timestamp", "")
            }

    async def clear_trailing_stop(self, ticker: str):
        async with self._lock:
            if ticker in self.trailing_stops:
                del self.trailing_stops[ticker]
                logger.info(f"🗑️ {ticker} 트레일링 스탑 상태 제거")