"""
scanner/deep_analyzer.py - v7.7.0 (P2-3: VaR 캐시 도입)
- 모든 하드코딩 값을 config.yaml에서 로드 (trading_* 키)
- ATR=0 방어, Almgren-Chriss 시장 충격, 3분할 체결, 4대 평가, 트레일링 스탑 100% 포함
- PortfolioManager 연동 + 락 최적화 (P1-2, P1-3)
- ML 피처 저장 지원 (P0-1)
- VaR 캐시 도입 (TTL 300초) → DB 조회 최적화 (P2-3)
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config import get_config
from core.debug_tower import debug_tower
from core.logger import setup_logger
from data.db_manager import DatabaseManager
from data.news_crawler import NewsCrawler
from decision.hybrid_decider import HybridDecider
from filters.dynamic_weighter import DynamicWeighter
from filters.korean_special_filter import KoreanSpecialFilter
from filters.macro_filter import MacroFilter
from filters.sector_filter import SectorFilter
from filters.stock_filter import StockFilter
from orchestrator.portfolio_manager import PortfolioManager
from orchestrator.strategy_router import StrategyRouter
from risk.var_calculator import VaRCalculator
from validation.execution_simulator import RealisticExecutionSimulator

logger = setup_logger("analyzer")
config = get_config()

CALIBRATION_FILE = Path(__file__).parent.parent / "config" / "calibration_config.json"


class DeepAnalyzer:
    def __init__(self, db_manager: DatabaseManager = None, feedback_learner: Any | None = None):
        self.db = db_manager
        self.feedback_learner = feedback_learner

        self.macro = MacroFilter()
        self.sector = SectorFilter()
        self.stock = StockFilter()
        self.korean = KoreanSpecialFilter()
        self.weighter = DynamicWeighter()
        self.decider = HybridDecider()
        self.weights = {"momentum": 1.0, "volume": 1.0, "volatility": 1.0, "macro": 1.0, "sector": 1.0}

        self.trailing_stops: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._last_atr_alert: dict[str, float] = {}
        self._atr_cooldown_seconds = 600

        self._ohlcv_cache: dict[str, dict] = {}
        self._cache_time: dict[str, float] = {}

        # 🔥 P2-3: VaR 캐시 (개별 종목 VaR 결과 TTL 300초)
        self._var_cache: dict[str, tuple[float, float]] = {}  # {ticker: (timestamp, risk_adj)}
        self._var_cache_ttl = 300  # 5분

        self._feedback_stats = {"win_rate": 0.5, "sharpe": 1.0, "sample_count": 0, "avg_return": 0.0}
        self._feedback_stats_loaded = False
        self._news_crawler = None

        self.exec_sim = RealisticExecutionSimulator(max_slippage_bps=100.0, num_slices=3)

        self.max_hold_hours = config.get_float("trading_max_hold_hours", 2.0)
        self.trail_aggressive_threshold = config.get_float("trading_trail_aggressive_threshold", 5.0)
        self.atr_multiplier_stop = config.get_float("trading_atr_multiplier_stop", 2.0)
        self.atr_multiplier_trail = config.get_float("trading_atr_multiplier_trail", 1.5)
        self.atr_spike_threshold = config.get_float("trading_atr_spike_threshold", 0.3)

        self.momentum_weight = config.get_float("trading_momentum_weight", 0.08)
        self.ml_weight = config.get_float("trading_ml_weight", 0.18)
        self.sentiment_weight = config.get_float("trading_sentiment_weight", 0.02)
        self.base_weight = config.get_float("trading_base_weight", 0.42)
        self.strategy_weight = config.get_float("trading_strategy_weight", 0.30)

        self._load_calibration_config()

        self.strategy_router = StrategyRouter()
        self.var_calc = VaRCalculator(
            confidence=config.get_float("risk_var_confidence", 0.95),
            window=config.get_int("risk_var_lookback_days", 252),
        )

                # 🔧 v7.7.1: PortfolioManager는 싱글톤(orchestrator/portfolio_manager.py의
        # __new__ 확인 완료)이며, app/bootstrap.py의 init_container()가
        # container.initialize() 내부에서 이미 start()를 호출합니다.
        # 여기서 asyncio.create_task()로 재차 시작을 시도하면 이미 실행 중인
        # 루프에 대해 즉시 반환되는 불필요한 태스크가 매번 생성되므로 제거하고
        # 참조만 보관합니다.
        # (주의: DeepAnalyzer를 V10 bootstrap 흐름 밖에서 단독 생성해 사용하는
        # 코드 경로가 있다면, 그 호출부에서 명시적으로
        # `await analyzer.portfolio_manager.start()`를 호출해야 합니다.)
        self.portfolio_manager = PortfolioManager()
        logger.debug("PortfolioManager 싱글톤 참조 획득 (시작/종료는 컨테이너가 전담)")


    def _load_calibration_config(self):
        default = {
            "FILL_RATIO_REJECT": config.get_float("trading_fill_ratio_reject", 0.30),
            "FILL_RATIO_REDUCE": config.get_float("trading_fill_ratio_reduce", 0.70),
            "ORDER_VOLUME_RATIO": config.get_float("trading_order_volume_ratio", 0.008),
            "ORDER_VOLUME_MIN": config.get_int("trading_order_volume_min", 10),
            "ORDER_VOLUME_MAX": config.get_int("trading_order_volume_max", 500),
        }
        if CALIBRATION_FILE.exists():
            try:
                with open(CALIBRATION_FILE, encoding="utf-8") as f:
                    cfg = json.load(f)
                    self.FILL_RATIO_REJECT = cfg.get("FILL_RATIO_REJECT", default["FILL_RATIO_REJECT"])
                    self.FILL_RATIO_REDUCE = cfg.get("FILL_RATIO_REDUCE", default["FILL_RATIO_REDUCE"])
                    self.ORDER_VOLUME_RATIO = cfg.get("ORDER_VOLUME_RATIO", default["ORDER_VOLUME_RATIO"])
                    self.ORDER_VOLUME_MIN = cfg.get("ORDER_VOLUME_MIN", default["ORDER_VOLUME_MIN"])
                    self.ORDER_VOLUME_MAX = cfg.get("ORDER_VOLUME_MAX", default["ORDER_VOLUME_MAX"])
                    logger.info(
                        f"✅ Calibration 설정 로드: REJECT={self.FILL_RATIO_REJECT:.1%}, REDUCE={self.FILL_RATIO_REDUCE:.1%}"
                    )
                    return
            except Exception as e:
                logger.warning(f"⚠️ Calibration 설정 로드 실패: {e}")
        self.FILL_RATIO_REJECT = default["FILL_RATIO_REJECT"]
        self.FILL_RATIO_REDUCE = default["FILL_RATIO_REDUCE"]
        self.ORDER_VOLUME_RATIO = default["ORDER_VOLUME_RATIO"]
        self.ORDER_VOLUME_MIN = default["ORDER_VOLUME_MIN"]
        self.ORDER_VOLUME_MAX = default["ORDER_VOLUME_MAX"]

    async def load_weights(self):
        if self.db:
            self.weights = await self.db.get_weights()
            self._feedback_stats = await self.db.get_feedback_stats(days=30)
            self._feedback_stats_loaded = True
            logger.info(f"📊 최신 가중치 로드: {self.weights}")
            debug_tower.log("SYSTEM", "WEIGHTS_LOADED", {"weights": self.weights})

    async def calculate_atr(self, ticker: str, period: int = 14) -> float:
        if not self.db:
            return 0.0
        try:
            ohlcv_list = await self.db.get_ohlcv(ticker, period)
            clean_list = [d for d in ohlcv_list if d.get("high", 0) > 0 and d.get("low", 0) > 0]
            if len(clean_list) < 2:
                return 0.0
            tr_values = []
            for i in range(1, len(clean_list)):
                high = clean_list[i]["high"]
                low = clean_list[i]["low"]
                prev_close = clean_list[i - 1]["close"]
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
            debug_tower.capture_snapshot(ticker, e, "ATR_CALC")
            return 0.0

    async def _get_avg_volume(self, ticker: str, period: int = 20) -> int:
        try:
            if not self.db:
                return 0
            data = await self.db.get_ohlcv(ticker, period)
            if len(data) < 2:
                return 0
            volumes = [d.get("volume", 0) for d in data if d.get("volume", 0) > 0]
            if not volumes:
                return 0
            return int(sum(volumes) / len(volumes))
        except Exception as e:
            logger.debug(f"평균 거래량 조회 실패 ({ticker}): {e}")
            return 0

    async def _get_sentiment_score(self, ticker: str) -> float:
        try:
            if self._news_crawler is None:
                self._news_crawler = NewsCrawler()
            _, sentiment = await self._news_crawler.get_news_with_sentiment(ticker, limit=5, cache_seconds=3600)
            return float(sentiment) if sentiment is not None else 0.0
        except Exception as e:
            logger.debug(f"감성 점수 조회 실패 ({ticker}): {e}")
            return 0.0

    async def _get_cached_ohlcv(self, ticker: str, period: int = 30) -> dict:
        now = time.time()
        cache_key = f"{ticker}_{period}"
        if cache_key in self._ohlcv_cache and (now - self._cache_time.get(cache_key, 0)) < 60:
            return self._ohlcv_cache[cache_key]
        if not self.db:
            return {}
        data = await self.db.get_ohlcv(ticker, period)
        if len(data) < 5:
            return {}
        closes = [d["close"] for d in data]
        volumes = [d.get("volume", 0) for d in data if d.get("volume", 0) > 0]

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
                diff = values[i] - values[i - 1]
                if diff > 0:
                    gains.append(diff)
                    losses.append(0)
                else:
                    gains.append(0)
                    losses.append(-diff)
            avg_gain = sum(gains[-n:]) / n if n > 0 else 0
            avg_loss = sum(losses[-n:]) / n if n > 0 else 0
            if avg_loss == 0:
                return 100
            rs = avg_gain / avg_loss
            return 100 - (100 / (1 + rs))

        avg_volume = sum(volumes) / len(volumes) if volumes else 1
        current_volume = volumes[-1] if volumes else 1
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
        result = {
            "current_price": float(data[-1]["close"]),
            "high": float(data[-1]["high"]),
            "low": float(data[-1]["low"]),
            "volume_ratio": float(volume_ratio),
            "avg_volume": int(avg_volume),
            "ema5": float(ema(closes, 5)),
            "ema20": float(ema(closes, 20)),
            "ema60": float(ema(closes, 60)) if len(closes) >= 60 else float(ema(closes, len(closes))),
            "rsi": float(rsi(closes, 14)),
        }
        self._ohlcv_cache[cache_key] = result
        self._cache_time[cache_key] = now
        return result

    # ============================================================
    # 🔥 P2-3: VaR 캐시 메서드
    # ============================================================
    async def _get_cached_var(self, ticker: str) -> float:
        """개별 종목 VaR 계산 (캐시 적용, TTL 300초)"""
        now = time.time()
        if ticker in self._var_cache:
            ts, risk_adj = self._var_cache[ticker]
            if now - ts < self._var_cache_ttl:
                return risk_adj

        # 포트폴리오 매니저에서 글로벌 패널티 우선 사용
        global_penalty = self.portfolio_manager.get_global_risk_penalty()

        risk_adj = 1.0
        if self.db:
            try:
                ohlcv_data = await self.db.get_ohlcv(ticker, period=252)
                if len(ohlcv_data) >= 30:
                    returns = []
                    for i in range(1, len(ohlcv_data)):
                        prev = ohlcv_data[i - 1].get("close", 0)
                        curr = ohlcv_data[i].get("close", 0)
                        if prev > 0 and curr > 0:
                            returns.append((curr - prev) / prev)
                    if len(returns) >= 30:
                        var_result = self.var_calc.calculate(returns)
                        risk_adj = var_result.get("risk_adjustment_factor", 1.0)
                        # 개별 VaR와 글로벌 패널티 결합 (개별이 더 높은 경우 적용)
                        risk_adj = min(risk_adj, global_penalty)
            except Exception as e:
                logger.debug(f"⚠️ VaR 계산 실패 ({ticker}): {e}")

        self._var_cache[ticker] = (now, risk_adj)
        return risk_adj

    # ============================================================
    # 평가 헬퍼 (기존 유지)
    # ============================================================
    def _evaluate_technical(self, ticker: str, entry_price: float, tech_data: dict, atr: float) -> dict:
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
            score += 0.4
            reasons.append("📈 EMA 정배열 (+0.4)")
        elif ema5 > ema20:
            score += 0.2
            reasons.append("📈 단기 상승 추세 (+0.2)")
        elif ema5 < ema20 and ema20 < ema60:
            score -= 0.4
            reasons.append("📉 EMA 역배열 (-0.4)")
        elif ema5 < ema20:
            score -= 0.2
            reasons.append("📉 단기 하락 추세 (-0.2)")
        if rsi > 80:
            score -= 0.7
            reasons.append(f"🔥 과매수 (RSI {rsi:.0f}) (-0.7)")
        elif rsi > 70:
            score -= 0.4
            reasons.append(f"⚠️ 과매수 임박 (RSI {rsi:.0f}) (-0.4)")
        elif rsi < 20:
            score += 0.7
            reasons.append(f"📉 과매도 (RSI {rsi:.0f}) (+0.7)")
        elif rsi < 30:
            score += 0.4
            reasons.append(f"📉 과매도 임박 (RSI {rsi:.0f}) (+0.4)")
        if volume_ratio > 2.0:
            score += 0.3
            reasons.append(f"📊 거래량 급증 (×{volume_ratio:.1f}) (+0.3)")
        elif volume_ratio < 0.5:
            score -= 0.2
            reasons.append(f"📊 거래량 부진 (×{volume_ratio:.1f}) (-0.2)")
        if atr > 0:
            move_ratio = abs(price - entry_price) / atr
            if move_ratio > 2.0:
                score += 0.3
                reasons.append(f"💪 강한 모멘텀 (ATR×{move_ratio:.1f}) (+0.3)")
        return {"score": max(-1.0, min(1.0, score)), "reasons": reasons[:3]}

    def _evaluate_risk(
        self,
        ticker: str,
        current_price: float,
        stop_price: float,
        atr: float,
        highest_price: float,
        feedback_stats: dict,
    ) -> dict:
        score = 0.0
        reasons = []
        win_rate = feedback_stats.get("win_rate", 0.5)
        sharpe = feedback_stats.get("sharpe", 1.0)
        risk_penalty = 1.2 if win_rate < 0.4 else (0.8 if win_rate > 0.6 else 1.0)
        if current_price > 0 and stop_price > 0:
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
        if highest_price > 0 and highest_price > current_price:
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

    def _evaluate_time_value(self, entry_time: str, current_price: float, entry_price: float) -> dict:
        score = 0.0
        reasons = []
        try:
            entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
            now = datetime.now()
            elapsed_hours = (now - entry_dt).total_seconds() / 3600
        except:
            elapsed_hours = 0
        if elapsed_hours < 0.01:
            return {"score": 0.0, "reasons": ["⏳ 진입 직전"]}
        pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price != 0 else 0
        annualized_return = (pnl_pct / elapsed_hours) * 24 * 365 if elapsed_hours > 0 else 0
        if annualized_return > 30:
            score += 0.7
            reasons.append(f"💰 연환산 수익률 {annualized_return:.0f}% (+0.7)")
        elif annualized_return > 15:
            score += 0.4
            reasons.append(f"💰 연환산 수익률 {annualized_return:.0f}% (+0.4)")
        elif annualized_return < -30:
            score -= 0.7
            reasons.append(f"📉 연환산 손실률 {annualized_return:.0f}% (-0.7)")
        if elapsed_hours > 1 and pnl_pct < 1.0:
            score -= 0.3
            reasons.append(f"⏳ 시간 대비 수익률 부진 ({elapsed_hours:.1f}시간) (-0.3)")
        return {"score": max(-1.0, min(1.0, score)), "reasons": reasons[:3]}

    def _evaluate_microstructure(self, ticker: str, tech_data: dict, imbalance: float) -> dict:
        score = 0.0
        reasons = []
        volume_ratio = tech_data.get("volume_ratio", 1.0) if tech_data else 1.0
        if imbalance > 0.6:
            score += 0.3
            reasons.append(f"⚖️ 매수 우세 ({imbalance:.0%}) (+0.3)")
        elif imbalance < 0.4:
            score -= 0.3
            reasons.append(f"⚖️ 매도 우세 ({imbalance:.0%}) (-0.3)")
        else:
            reasons.append(f"⚖️ 중립 ({imbalance:.0%}) (0.0)")
        if volume_ratio > 1.5:
            score += 0.3
            reasons.append(f"📊 거래량 활발 (×{volume_ratio:.1f}) (+0.3)")
        elif volume_ratio < 0.6:
            score -= 0.2
            reasons.append(f"📊 거래량 부진 (×{volume_ratio:.1f}) (-0.2)")
        return {"score": max(-1.0, min(1.0, score)), "reasons": reasons[:3]}

    def _run_consensus(self, state: dict, current_price: float, atr: float, tech_data: dict, imbalance: float) -> dict:
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
            tech["score"] * weights["technical"]
            + risk["score"] * weights["risk"]
            + time_val["score"] * weights["time_value"]
            + micro["score"] * weights["micro"]
        )
        total_weight = sum(weights.values())
        consensus_score = max(-1.0, min(1.0, weighted_sum / total_weight))
        votes = {
            "technical": tech["score"],
            "risk": risk["score"],
            "time_value": time_val["score"],
            "micro": micro["score"],
        }
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
            "summary": summary,
        }

    def _check_tp_hit(self, state: dict, current_price: float) -> dict | None:
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
                    "recommendation_reason": "TP1 도달 → 50% 청산",
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
                    "recommendation_reason": "TP2 도달 → 추가 30% 청산",
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
                    "recommendation_reason": "TP3 도달 → 전량 청산",
                }
        return None

    # ============================================================
    # 트레일링 스탑 (P1-2: 락 외부 I/O)
    # ============================================================
    async def _update_trailing_stop(
        self, ticker: str, current_price: float, atr: float, tech_data: dict, imbalance: float
    ) -> dict | None:
        current_price = self._to_float(current_price, 0.0)
        atr = self._to_float(atr, 0.0)

        if not tech_data and self.db:
            tech_data = await self._get_cached_ohlcv(ticker, 30)

        async with self._lock:
            state = self.trailing_stops.get(ticker)
            if not state:
                return None
            action = state.get("action")
            entry_price = self._to_float(state.get("entry_price"), 0.0)
            current_stop = self._to_float(state.get("current_stop"), 0.0)
            highest_price = self._to_float(state.get("highest_price"), entry_price)
            lowest_price = self._to_float(state.get("lowest_price"), entry_price)
            prev_atr = self._to_float(state.get("atr"), atr)
            tp_hit_level = state.get("tp_hit_level", 0)
            state["last_update_time"] = datetime.now().isoformat()
            updated = False
            new_stop = current_stop
            if action == "BUY":
                if current_price > highest_price:
                    highest_price = current_price
                    state["highest_price"] = highest_price
                    pnl_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price != 0 else 0
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
                    pnl_pct = ((entry_price - current_price) / entry_price) * 100 if entry_price != 0 else 0
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
                    if tp_hit_level < 2 and entry_price > 0:
                        if action == "BUY":
                            state["tp2_price"] = entry_price + (atr * 5.0)
                            state["tp3_price"] = entry_price + (atr * 7.0)
                        else:
                            state["tp2_price"] = entry_price - (atr * 5.0)
                            state["tp3_price"] = entry_price - (atr * 7.0)
                        tp_adjusted = True
                        logger.info(
                            f"🔄 {ticker} 동적 TP 조정: TP2 {state['tp2_price']:.0f}, TP3 {state['tp3_price']:.0f}"
                        )
                        debug_tower.log(ticker, "TP_ADJUSTED", {"tp2": state["tp2_price"], "tp3": state["tp3_price"]})
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
                    "pnl": ((current_price - entry_price) / entry_price * 100)
                    if action == "BUY" and entry_price != 0
                    else ((entry_price - current_price) / entry_price * 100)
                    if action == "SELL" and entry_price != 0
                    else 0,
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
                    "atr_change_ratio": abs(atr - prev_atr) / prev_atr if prev_atr != 0 else 0,
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
                    "pnl": ((current_price - entry_price) / entry_price * 100)
                    if action == "BUY" and entry_price != 0
                    else ((entry_price - current_price) / entry_price * 100)
                    if action == "SELL" and entry_price != 0
                    else 0,
                    "advice": consensus,
                    "timestamp": datetime.now().isoformat(),
                }
            return None

    def _create_exit_signal(self, ticker: str, state: dict, reason: str) -> dict:
        action = state.get("action")
        entry_price = state.get("entry_price")
        current_stop = state.get("current_stop")
        current_price = state.get("last_price", entry_price)
        pnl = (
            ((current_price - entry_price) / entry_price * 100)
            if action == "BUY" and entry_price != 0
            else ((entry_price - current_price) / entry_price * 100)
            if action == "SELL" and entry_price != 0
            else 0
        )
        del self.trailing_stops[ticker]
        debug_tower.log(ticker, "EXIT_SIGNAL", {"reason": reason, "pnl": pnl})
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

    def _to_float(self, value: Any, default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    # ============================================================
    # 메인 분석 (P2-3: VaR 캐시 적용)
    # ============================================================
    async def analyze(self, stock: dict) -> dict:
        try:
            ticker = stock.get("ticker", "")
            trace_id = stock.get("trace_id", f"T-{ticker}-{int(time.time() * 1000)}")
            debug_tower.log(ticker, "ANALYZE_START", {"price": stock.get("price")}, trace_id)

            current_price = self._to_float(stock.get("price"), 0.0)
            entry_price = self._to_float(stock.get("entry_price", current_price), current_price)
            imbalance = self._to_float(stock.get("imbalance"), 0.5)
            if imbalance < 0 or imbalance > 1:
                imbalance = 0.5
            regime = stock.get("regime", "Sideways")

            tech_data = await self._get_cached_ohlcv(ticker, 30) if self.db else {}
            atr_raw = await self.calculate_atr(ticker, 14) if self.db else 0.0
            atr = self._to_float(atr_raw, 0.0)

            macro_score = self.macro.check(stock)
            sector_score = self.sector.check(stock)
            stock_score = self.stock.check(stock, regime=regime, atr=atr)
            korean_score = self.korean.check(stock)

            weights = self.weighter.calculate({"regime": regime, "flow": stock.get("flow", {})})

            base_score = (
                macro_score["score"] * weights.get("trend_weight", 0.3)
                + sector_score["score"] * weights.get("risk_weight", 0.2)
                + stock_score["score"] * weights.get("flow_weight", 0.4)
                + korean_score["score"] * 0.1
            )

            sentiment_score = await self._get_sentiment_score(ticker)
            sentiment_factor = max(0.0, min(1.0, (sentiment_score + 1) / 2))
            momentum = stock.get("momentum", 0.0)
            momentum_score = max(0.0, min(1.0, 0.5 + abs(momentum) * 10))

            ml_score = 0.5
            if self.feedback_learner and self.feedback_learner._model_ready:
                try:
                    features_for_ml = {
                        "momentum": momentum,
                        "rsi": tech_data.get("rsi", 50) if tech_data else 50,
                        "volume_ratio": tech_data.get("volume_ratio", 1.0) if tech_data else 1.0,
                        "macro_score": macro_score.get("score", 0.5),
                        "sector_score": sector_score.get("score", 0.5),
                        "imbalance": imbalance,
                    }
                    ml_score = self.feedback_learner.predict_prob(features_for_ml)
                    ml_score = max(0.1, min(0.9, ml_score))
                except Exception as e:
                    logger.debug(f"⚠️ ML 예측 실패 ({ticker}): {e}")
                    ml_score = 0.5

            strategy_data = {
                "ticker": ticker,
                "price": current_price,
                "tech_data": tech_data,
                "regime": regime,
                "atr": atr,
                "high_52w": stock.get("high_52w", current_price * 1.2),
                "low_52w": stock.get("low_52w", current_price * 0.8),
                "bb_upper": stock.get("bb_upper", current_price * 1.05),
                "bb_lower": stock.get("bb_lower", current_price * 0.95),
                "adx": stock.get("adx", 20),
            }
            strategy_result = await self.strategy_router.route(strategy_data)
            strategy_score = strategy_result["final_score"]
            strategy_action = strategy_result["final_action"]
            strategy_confidence = strategy_result["final_confidence"]

            final_score = (
                base_score * self.base_weight
                + momentum_score * self.momentum_weight
                + sentiment_factor * self.sentiment_weight
                + ml_score * self.ml_weight
                + strategy_score * self.strategy_weight
            )
            final_score = max(0.0, min(1.0, final_score))

            # 🔥 P2-3: VaR 캐시 사용
            risk_adj = await self._get_cached_var(ticker)

            if strategy_action in ["BUY", "SELL"] and strategy_confidence > 0.6:
                final_action = strategy_action
                final_confidence = strategy_confidence
                _ = f"전략 합의: {strategy_result['consensus']}"
            else:
                decision = self.decider.decide(
                    {
                        "score": final_score,
                        "macro": macro_score,
                        "sector": sector_score,
                        "stock": stock_score,
                        "korean": korean_score,
                    }
                )
                final_action = decision.get("action", "HOLD")
                final_confidence = decision.get("confidence", 0.5)
                _ = "점수 기반 결정"

            original_action_label = {"BUY": "매수", "SELL": "매도", "HOLD": "관망"}.get(final_action, "관망")

            positives = [f"전략: {strategy_result['consensus']}"]
            if final_score > 0.6:
                positives.append(f"종합 점수 {final_score:.1%}")
            if ml_score != 0.5:
                positives.append(f"ML 예측 {ml_score:.1%}")
            if tech_data:
                positives.append(f"RSI {tech_data.get('rsi', 50):.0f}")
            if regime:
                positives.append(f"국면: {regime}")

            strategy_details = []
            for r in strategy_result.get("strategy_results", []):
                strategy_details.append(f"{r.name}: {r.action} ({r.score:.0%})")

            # P0-1 연계: ML 학습용 features 구성
            features = {
                "momentum": momentum,
                "rsi": tech_data.get("rsi", 50) if tech_data else 50,
                "volume_ratio": tech_data.get("volume_ratio", 1.0) if tech_data else 1.0,
                "macro_score": macro_score.get("score", 0.5),
                "sector_score": sector_score.get("score", 0.5),
                "imbalance": imbalance,
            }

            result = {
                "ticker": ticker,
                "name": stock.get("name", stock.get("ticker", "")),
                "price": current_price,
                "action": final_action,
                "action_label": original_action_label,
                "original_action": final_action,
                "score": final_score,
                "confidence": final_confidence,
                "positives": positives,
                "negatives": decision.get("risks", ["시장 변동성 주의"]) if final_action != "HOLD" else ["관망 유지"],
                "counterfactuals": decision.get("counterfactuals", []),
                "imbalance": imbalance,
                "atr": atr,
                "entry_price": entry_price,
                "sentiment_score": sentiment_score,
                "momentum": momentum,
                "momentum_score": momentum_score,
                "ml_score": ml_score,
                "risk_adjustment_factor": risk_adj,
                "strategy_result": {
                    "final_score": strategy_score,
                    "final_action": strategy_action,
                    "confidence": strategy_confidence,
                    "consensus": strategy_result.get("consensus", ""),
                    "details": strategy_details,
                },
                "regime": regime,
                "details": {
                    "macro": macro_score["score"],
                    "sector": sector_score["score"],
                    "stock": stock_score["score"],
                    "korean": korean_score["score"],
                    "imbalance": imbalance,
                    "atr": atr,
                    "sentiment": sentiment_factor,
                    "momentum": momentum,
                    "momentum_score": momentum_score,
                    "ml_score": ml_score,
                    "strategy_score": strategy_score,
                    "risk_adj": risk_adj,
                    "regime": regime,
                },
                "timestamp": stock.get("timestamp", ""),
                "features": features,  # DB 저장용
            }

            # 체결 시뮬레이션 (기존 로직 유지)
            orderbook = stock.get("orderbook", {})
            if orderbook and result["action"] in ["BUY", "SELL"]:
                avg_volume = tech_data.get("avg_volume", 0) if tech_data else 0
                if avg_volume > 0:
                    order_volume = int(avg_volume * self.ORDER_VOLUME_RATIO)
                    order_volume = max(self.ORDER_VOLUME_MIN, min(self.ORDER_VOLUME_MAX, order_volume))
                else:
                    order_volume = 100
                market_cap = stock.get("market_cap", 1e12)
                sim_result = self.exec_sim.execute(
                    ticker=ticker,
                    action=result["action"],
                    price=current_price,
                    volume=order_volume,
                    order_size=order_volume,
                    market_cap=market_cap,
                    avg_daily_volume=avg_volume,
                    current_time=datetime.now(),
                    orderbook=orderbook,
                )
                if sim_result.filled:
                    fill_ratio = sim_result.fill_ratio
                    adjusted_entry = sim_result.execution_price
                    if fill_ratio < self.FILL_RATIO_REJECT:
                        logger.warning(f"⚠️ {ticker} 체결률 {fill_ratio:.1%} → HOLD")
                        result["action"] = "HOLD"
                        result["action_label"] = "체결률 부족으로 보류"
                        result["negatives"].append(f"⚠️ 체결률 {fill_ratio:.1%}")
                    elif fill_ratio < self.FILL_RATIO_REDUCE:
                        original_conf = result.get("confidence", 0.6)
                        multiplier = (
                            0.65
                            + (fill_ratio - self.FILL_RATIO_REJECT)
                            / (self.FILL_RATIO_REDUCE - self.FILL_RATIO_REJECT)
                            * 0.35
                        )
                        result["confidence"] = max(0.3, original_conf * multiplier)
                        result["entry_price"] = adjusted_entry
                        result["slippage_bps"] = sim_result.slippage_bps
                        result["fill_ratio"] = fill_ratio
                    else:
                        result["entry_price"] = adjusted_entry
                        result["slippage_bps"] = sim_result.slippage_bps
                        result["fill_ratio"] = fill_ratio
                else:
                    logger.warning(f"⚠️ {ticker} 체결 불가 → HOLD")
                    result["action"] = "HOLD"
                    result["action_label"] = "체결 불가로 보류"
                    result["negatives"].append(f"⚠️ 체결 불가: {sim_result.reason}")

            # 포지션 진입 처리 (P1-3: await 추가)
            if result["action"] in ["BUY", "SELL"]:
                async with self._lock:
                    if ticker not in self.trailing_stops:
                        entry_price_f = self._to_float(result.get("entry_price", entry_price), 0.0)
                        atr_f = self._to_float(atr, 0.0)
                        if atr_f <= 0:
                            atr_f = entry_price_f * 0.01
                        tp1 = (
                            entry_price_f + (atr_f * 3.0)
                            if result["action"] == "BUY"
                            else entry_price_f - (atr_f * 3.0)
                        )
                        tp2 = (
                            entry_price_f + (atr_f * 5.0)
                            if result["action"] == "BUY"
                            else entry_price_f - (atr_f * 5.0)
                        )
                        tp3 = (
                            entry_price_f + (atr_f * 7.0)
                            if result["action"] == "BUY"
                            else entry_price_f - (atr_f * 7.0)
                        )
                        stop_price = (
                            entry_price_f - (atr_f * self.atr_multiplier_stop)
                            if result["action"] == "BUY"
                            else entry_price_f + (atr_f * self.atr_multiplier_stop)
                        )
                        self.trailing_stops[ticker] = {
                            "action": result["action"],
                            "ticker": ticker,
                            "entry_price": entry_price_f,
                            "current_stop": stop_price,
                            "tp1_price": tp1,
                            "tp2_price": tp2,
                            "tp3_price": tp3,
                            "highest_price": entry_price_f if result["action"] == "BUY" else None,
                            "lowest_price": entry_price_f if result["action"] == "SELL" else None,
                            "atr": atr_f,
                            "entry_time": datetime.now().isoformat(),
                            "last_update_time": datetime.now().isoformat(),
                            "last_advice_time": 0,
                            "tp_hit_level": 0,
                            "remaining_qty": 1.0,
                            "last_price": current_price,
                        }
                        logger.info(f"✅ {ticker} 포지션 추적 시작 (액션: {result['action']}, TP1:{tp1:.0f})")
                        debug_tower.log(ticker, "POSITION_START", {"action": result["action"]}, trace_id)
                        result["current_stop"] = stop_price
                        result["trailing_active"] = True
                        result["entry_time"] = datetime.now().isoformat()
                        result["side"] = result["action"]
                        result["action"] = "SIGNAL_ENTRY"
                        result["max_hold_hours"] = self.max_hold_hours
                        result["tp1"] = tp1
                        result["tp2"] = tp2
                        result["tp3"] = tp3

                        try:
                            await self.portfolio_manager.update_position(
                                ticker=ticker,
                                price=current_price,
                                qty=100,
                                entry_price=entry_price_f,
                                action=result["action"],
                            )
                            logger.debug(f"📈 {ticker} PortfolioManager에 포지션 등록 완료")
                        except Exception as e:
                            logger.warning(f"⚠️ PortfolioManager 업데이트 실패: {e}")

            elif ticker in self.trailing_stops:
                async with self._lock:
                    state = self.trailing_stops.get(ticker)
                    if state:
                        state["last_price"] = current_price
                    try:
                        await self.portfolio_manager.update_position(
                            ticker=ticker, price=current_price, qty=0, action="HOLD"
                        )
                    except Exception as e:
                        logger.debug(f"⚠️ PortfolioManager 가격 업데이트 실패: {e}")

                event = await self._update_trailing_stop(ticker, current_price, atr, tech_data, imbalance)
                if event:
                    debug_tower.log(ticker, "EVENT_GENERATED", {"event": event["action"]}, trace_id)
                    return event

            debug_tower.log(
                ticker,
                "ANALYZE_COMPLETE",
                {
                    "action": result.get("action"),
                    "score": result.get("score"),
                    "ml_score": ml_score,
                    "risk_adj": risk_adj,
                    "strategy_consensus": strategy_result.get("consensus"),
                },
                trace_id,
            )
            return result

        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            debug_tower.capture_snapshot(stock.get("ticker", "SYSTEM"), e, "ANALYZE")
            return {
                "ticker": stock.get("ticker", ""),
                "action": "ERROR",
                "score": 0.0,
                "confidence": 0.0,
                "positives": [],
                "negatives": [],
                "atr": 0.0,
                "ml_score": 0.5,
                "risk_adjustment_factor": 1.0,
                "error": str(e),
            }

    # ============================================================
    # P1-3: clear_trailing_stop (await 추가)
    # ============================================================
    async def clear_trailing_stop(self, ticker: str):
        async with self._lock:
            if ticker in self.trailing_stops:
                del self.trailing_stops[ticker]
                try:
                    await self.portfolio_manager.update_position(ticker=ticker, price=0, qty=0, action="EXIT")
                    logger.info(f"🗑️ {ticker} 트레일링 스탑 및 포트폴리오 제거")
                except Exception as e:
                    logger.warning(f"⚠️ PortfolioManager 포지션 제거 실패: {e}")
                debug_tower.log(ticker, "CLEAR_STOP", {})
