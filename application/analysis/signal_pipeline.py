# -*- coding: utf-8 -*-
"""
application/analysis/signal_pipeline.py - V10 Strategy Orchestration Pipeline v2.1

변경 이력 (v2.1 - Session 15):
    - 하이퍼파라미터 8개 전체 동적화 (인스턴스 변수 + update/get API)
    - update_hyperparameters(): buy/sell 임계값, min_confidence,
      SQI v2 가중치 2개(momentum/confidence, consensus는 자동 계산),
      전략 가중치 3개(trend/reversal/breakout)
    - get_hyperparameters(): 현재 적용 중인 전체 파라미터 반환 (헬스체크/로깅용)
    - _PARAM_TO_STRATEGY_TYPE: 클래스 타입(isinstance) 기반 전략 가중치 매칭
      (전략의 .name이 "Trend"인지 "TrendStrategy"인지 구현에 따라 다를 수 있어
       문자열 키 매칭은 실패 위험이 있으므로 타입 매칭으로 안전하게 처리)
    - compute_sqi_v2(): momentum_w/confidence_w/consensus_w 선택적 인자 추가
      (기본값 = 기존 모듈 상수 → 하위 호환)
    - _ensemble(): compute_sqi_v2() 호출 시 인스턴스 가중치 전달
    - _combine_scores(): 모듈 상수 대신 self.buy_threshold/self.sell_threshold 사용
    - process(): 모듈 상수 대신 self.min_confidence 사용
    - _collect_evidence(): @staticmethod → 인스턴스 메서드로 전환,
      self.buy_threshold/self.sell_threshold 참조 (표시 근거와 실제 판단 기준 일치)

변경 이력 (v2.0):
    - 신뢰도 기반 동적 가중치 앙상블 (confidence-weighted ensemble)
    - 다수결 + 신뢰도 임계값 기반 최종 Action 판정
    - Strategy.weight 프로퍼티 완전 활용
    - Bollinger Band, MACD 계산을 _fetch_ohlcv에 추가
    - Signal Quality Index (SQI) 도입
    - 전략 합의도(consensus) 측정 및 로깅
    - 타입 힌트 100% + Google Style Docstrings

Architecture:
    Data → Filters(base_score) → DomainStrategies(strategy_score)
         → ConfidenceWeightedEnsemble → ConsensusVoting → Signal
"""

import asyncio
import math
import time
from typing import Dict, Any, Optional, Callable, List, Tuple

from config.schema import get_config
from core.logger import setup_logger

# Domain models
from domain.models.signal import Action, Signal

# Domain strategies
from domain.strategies.trend import TrendStrategy
from domain.strategies.reversal import ReversalStrategy
from domain.strategies.breakout import BreakoutStrategy
from domain.strategies.base import Strategy, StrategyResult

# Application services
from application.analysis.atr_service import AtrService

# Existing filters (kept for gradual migration)
from filters.macro_filter import MacroFilter
from filters.sector_filter import SectorFilter
from filters.stock_filter import StockFilter
from filters.korean_special_filter import KoreanSpecialFilter
from filters.dynamic_weighter import DynamicWeighter

# Tracing
from observability.auto_trace import TracedService
from observability.tracer import get_tracer

logger = setup_logger("signal_pipeline")
config = get_config()
trace = get_tracer(__name__)

# ─── 앙상블 상수 (초기 기본값 — 런타임에 인스턴스 변수로 오버라이드됨) ──
_BUY_THRESHOLD = 0.62   # 최종 스코어 BUY 임계값
_SELL_THRESHOLD = 0.38  # 최종 스코어 SELL 임계값
_MIN_CONFIDENCE = 0.45  # 최소 신뢰도 (이하면 HOLD 강제)
_MIN_CONSENSUS = 0.50   # 다수결 합의도 임계값 (튜너 탐색 대상 아님 → 상수 유지)

# ─── SQI v2 가중치 상수 (초기 기본값) ────────────────────────────────
_SQI_V2_MOMENTUM_W = 0.30    # 모멘텀 스코어 가중치
_SQI_V2_CONFIDENCE_W = 0.40  # 신뢰도 가중치
_SQI_V2_CONSENSUS_W = 0.30   # 합의도 가중치
_SQI_V2_MIN = 0.0             # SQI v2 하한
_SQI_V2_MAX = 1.0             # SQI v2 상한

# ─── 전략 타입 → 파라미터 키 매핑 ────────────────────────────────────
# 문자열 이름 매칭 대신 isinstance() 기반으로 안전하게 전략 가중치를 갱신합니다.
_PARAM_TO_STRATEGY_TYPE: Dict[str, type] = {
    "trend_weight": TrendStrategy,
    "reversal_weight": ReversalStrategy,
    "breakout_weight": BreakoutStrategy,
}


class EnsembleResult:
    """전략 앙상블 결과 DTO

    Attributes:
        score: 가중 평균 최종 스코어 (0~1)
        confidence: 가중 평균 신뢰도 (0~1)
        action: 다수결 최종 Action 문자열
        consensus: 다수결 합의도 (0~1, 높을수록 전략 일치)
        sqi: Signal Quality Index v1 = confidence × consensus (0~1)
        sqi_v2: Signal Quality Index v2 = weighted(momentum,confidence,consensus)
                × volume_boost × volatility_penalty (0~1)
        details: 각 전략 결과 요약 리스트
    """

    __slots__ = ("score", "confidence", "action", "consensus", "sqi", "sqi_v2", "details")

    def __init__(
        self,
        score: float,
        confidence: float,
        action: str,
        consensus: float,
        sqi: float,
        details: List[str],
        sqi_v2: float = 0.0,
    ) -> None:
        self.score = score
        self.confidence = confidence
        self.action = action
        self.consensus = consensus
        self.sqi = sqi
        self.sqi_v2 = sqi_v2
        self.details = details


class SignalPipeline(TracedService):
    """Signal 처리 파이프라인 (전략 오케스트레이터)

    V10 DDD 계층의 Application Service 역할.
    기존 레거시 필터(Macro/Sector/Stock/Korean)와 신규 Domain Strategy를
    신뢰도 기반 앙상블로 결합해 최종 Signal을 생성합니다.

    v2.1 신규:
        하이퍼파라미터 8개를 인스턴스 변수로 관리하여 런타임 동적 갱신 가능.
        HyperparameterTuner.apply_to_pipeline() 또는
        update_hyperparameters() 직접 호출로 파라미터를 교체할 수 있습니다.

    Attributes:
        db_manager: 데이터베이스 관리자 (선택적)
        strategies: 도메인 전략 목록 (Trend, Reversal, Breakout)
        atr_service: ATR 계산 서비스
        base_weight: 레거시 필터 스코어 가중치
        strategy_weight: 도메인 전략 스코어 가중치
        buy_threshold: BUY 판정 임계값 (동적 갱신 가능)
        sell_threshold: SELL 판정 임계값 (동적 갱신 가능)
        min_confidence: HOLD 강제 최소 신뢰도 (동적 갱신 가능)
        sqi_v2_momentum_w: SQI v2 모멘텀 가중치 (동적 갱신 가능)
        sqi_v2_confidence_w: SQI v2 신뢰도 가중치 (동적 갱신 가능)
        sqi_v2_consensus_w: SQI v2 합의도 가중치 (자동 계산)
    """

    def __init__(
        self,
        db_manager=None,
        atr_service: Optional[AtrService] = None,
        realtime_price_provider: Optional[Callable[[str], float]] = None,
        strategies: Optional[List[Strategy]] = None,
    ) -> None:
        """SignalPipeline 초기화.

        Args:
            db_manager: DB 접근 객체 (선택적)
            atr_service: ATR 서비스 (None이면 자동 생성)
            realtime_price_provider: 실시간 가격 콜백 (선택적)
            strategies: 사용자 정의 전략 목록 (None이면 기본값 사용)
        """
        self.db_manager = db_manager
        self._realtime_price_provider = realtime_price_provider

        # ─── 레거시 필터 (점진적 교체 예정) ──────────────────────
        self.macro_filter = MacroFilter()
        self.sector_filter = SectorFilter()
        self.stock_filter = StockFilter()
        self.korean_filter = KoreanSpecialFilter()
        self.weighter = DynamicWeighter()

        # ─── 도메인 전략 (교체/추가 가능한 플러그인 구조) ──────────
        self.strategies: List[Strategy] = strategies or [
            TrendStrategy(weight=0.40),
            ReversalStrategy(),
            BreakoutStrategy(),
        ]

        # ─── ATR 서비스 ──────────────────────────────────────────
        self.atr_service = atr_service or AtrService(
            db_manager=db_manager,
            realtime_price_provider=realtime_price_provider,
        )

        # ─── 설정에서 가중치 로드 ─────────────────────────────────
        self.base_weight: float = config.trading.base_weight
        self.strategy_weight: float = config.trading.strategy_weight
        self.momentum_weight: float = config.trading.momentum_weight
        self.sentiment_weight: float = config.trading.sentiment_weight
        self.ml_weight: float = config.trading.ml_weight

        # ─── 🆕 v2.1: 동적 하이퍼파라미터 (초기값 = 모듈 상수) ───
        # HyperparameterTuner.apply_to_pipeline() 또는
        # update_hyperparameters()로 런타임에 갱신됩니다.
        self.buy_threshold: float = _BUY_THRESHOLD
        self.sell_threshold: float = _SELL_THRESHOLD
        self.min_confidence: float = _MIN_CONFIDENCE
        self.sqi_v2_momentum_w: float = _SQI_V2_MOMENTUM_W
        self.sqi_v2_confidence_w: float = _SQI_V2_CONFIDENCE_W
        self.sqi_v2_consensus_w: float = _SQI_V2_CONSENSUS_W

    def set_realtime_price_provider(self, provider: Callable[[str], float]) -> None:
        """실시간 가격 제공자 설정.

        Args:
            provider: ticker → 현재가 반환 함수
        """
        self._realtime_price_provider = provider
        if self.atr_service:
            self.atr_service.set_realtime_price_provider(provider)

    # ═══════════════════════════════════════════════════════════════
    #  🆕 v2.1: HyperparameterTuner 연동 API
    # ═══════════════════════════════════════════════════════════════

    def update_hyperparameters(self, params: Dict[str, float]) -> Dict[str, float]:
        """HyperparameterTuner의 TuningResult.best_params를 받아 즉시 반영.

        지원 파라미터 키 (모두 선택적 — 없으면 기존값 유지):
            buy_threshold, sell_threshold, min_confidence,
            sqi_v2_momentum_w, sqi_v2_confidence_w,
            trend_weight, reversal_weight, breakout_weight

        SQI v2 가중치 정규화 규칙:
            momentum_w + confidence_w ≤ 1.0 → consensus_w = 1.0 - (mom + conf)
            momentum_w + confidence_w > 1.0 → 비율 유지하며 합이 1.0이 되도록 정규화,
                                               consensus_w = 0.0

        전략 가중치 반영 방식:
            클래스 타입(isinstance)으로 매칭 → 이름 문자열 불일치 위험 없음

        Args:
            params: 갱신할 파라미터 딕셔너리
                    (HyperparameterTuner.TuningResult.best_params 형식)

        Returns:
            dict: 실제 적용된 전체 하이퍼파라미터 (get_hyperparameters() 결과)

        Raises:
            ValueError: buy_threshold <= sell_threshold 이거나
                        min_confidence가 (0, 1) 범위를 벗어난 경우
        """
        new_buy = params.get("buy_threshold", self.buy_threshold)
        new_sell = params.get("sell_threshold", self.sell_threshold)
        new_conf = params.get("min_confidence", self.min_confidence)

        if new_buy <= new_sell:
            raise ValueError(
                f"buy_threshold({new_buy:.3f})는 sell_threshold({new_sell:.3f})보다 커야 합니다"
            )
        if not (0.0 < new_conf < 1.0):
            raise ValueError(
                f"min_confidence({new_conf:.3f})는 (0, 1) 범위여야 합니다"
            )

        self.buy_threshold = new_buy
        self.sell_threshold = new_sell
        self.min_confidence = new_conf

        # ── SQI v2 가중치 정규화 ─────────────────────────────────
        mom_w = params.get("sqi_v2_momentum_w", self.sqi_v2_momentum_w)
        conf_w = params.get("sqi_v2_confidence_w", self.sqi_v2_confidence_w)
        total = mom_w + conf_w
        if total > 1.0:
            mom_w, conf_w = mom_w / total, conf_w / total
            cons_w = 0.0
        else:
            cons_w = max(0.0, 1.0 - total)
        self.sqi_v2_momentum_w = mom_w
        self.sqi_v2_confidence_w = conf_w
        self.sqi_v2_consensus_w = cons_w

        # ── 전략 가중치: 클래스 타입으로 안전하게 매칭 ───────────
        for param_key, strategy_type in _PARAM_TO_STRATEGY_TYPE.items():
            if param_key not in params:
                continue
            for s in self.strategies:
                if isinstance(s, strategy_type):
                    s.weight = params[param_key]
                    break

        result = self.get_hyperparameters()
        logger.info("SignalPipeline 하이퍼파라미터 갱신 완료: %s", result)
        return result

    def get_hyperparameters(self) -> Dict[str, float]:
        """현재 적용 중인 전체 하이퍼파라미터 반환.

        헬스체크(/health 엔드포인트), 로깅, 튜닝 결과 비교에 사용합니다.

        Returns:
            dict: buy_threshold, sell_threshold, min_confidence,
                  sqi_v2_momentum_w, sqi_v2_confidence_w, sqi_v2_consensus_w,
                  trend_weight, reversal_weight, breakout_weight
        """
        weights: Dict[str, float] = {}
        for param_key, strategy_type in _PARAM_TO_STRATEGY_TYPE.items():
            for s in self.strategies:
                if isinstance(s, strategy_type):
                    weights[param_key] = s.weight
                    break
        return {
            "buy_threshold": self.buy_threshold,
            "sell_threshold": self.sell_threshold,
            "min_confidence": self.min_confidence,
            "sqi_v2_momentum_w": self.sqi_v2_momentum_w,
            "sqi_v2_confidence_w": self.sqi_v2_confidence_w,
            "sqi_v2_consensus_w": self.sqi_v2_consensus_w,
            **weights,
        }

    # ═══════════════════════════════════════════════════════════════
    #  Public: SQI v2 정적 메서드 (외부에서 직접 호출 가능)
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def compute_sqi_v2(
        momentum_score: float,
        volume_ratio: float,
        bb_pct: float,
        confidence: float,
        consensus: float,
    ) -> float:
        """Signal Quality Index v2 계산 (모듈 수준 함수 위임, 기본 가중치 사용).

        Args:
            momentum_score: RSI+MACD 기반 모멘텀 품질 (0~1)
            volume_ratio: 현재 거래량 / 평균 거래량 (0 이상, 1.0=기준)
            bb_pct: Bollinger %B 값 (0~1)
            confidence: 전략 가중 평균 신뢰도 (0~1)
            consensus: 다수결 합의도 (0~1)

        Returns:
            float: SQI v2 값 (0~1)
        """
        return compute_sqi_v2(momentum_score, volume_ratio, bb_pct, confidence, consensus)

    # ═══════════════════════════════════════════════════════════════
    #  Public API
    # ═══════════════════════════════════════════════════════════════

    async def process(self, data: Dict[str, Any]) -> Signal:
        """단일 틱 데이터를 처리해 Signal을 생성합니다.

        처리 순서:
            1. 컨텍스트 추출 및 유효성 검사
            2. ATR 계산 (fallback: price × 1%)
            3. OHLCV 기술 지표 데이터 확보
            4. 레거시 필터 스코어 계산 (base_score)
            5. 도메인 전략 병렬 실행
            6. 신뢰도 기반 가중 앙상블 + 다수결 판정
            7. 최종 스코어 결합 및 Action 결정
            8. Signal 생성 및 반환

        Args:
            data: 원시 시장 데이터 딕셔너리
                - ticker (str): 종목 코드 (6자리)
                - price (float): 현재가
                - regime (str): 시장 레짐 (Bullish/Bearish/Sideways)
                - tech_data (dict): 기술 지표 캐시 (선택적)
                - trace_id (str): 추적 ID (선택적)

        Returns:
            Signal: 분석 결과 (Action, Score, Confidence, SQI 등 포함)
        """
        ticker: str = data.get("ticker", "")
        price: float = float(data.get("price", 0))
        trace_id: str = data.get("trace_id", f"PIPE-{ticker}-{int(time.time()*1000)}")
        regime: str = data.get("regime", "Sideways")

        if price <= 0:
            return Signal.error(ticker, "Invalid price", trace_id)

        atr = await self.atr_service.calculate(ticker, period=14)
        if atr == 0:
            atr = price * 0.01
            trace.warning("ATR fallback to 1% of price", ticker=ticker, atr=atr)

        tech_data: Dict[str, Any] = data.get("tech_data", {})
        if not tech_data and self.db_manager:
            tech_data = await self._fetch_ohlcv(ticker, 30)
        data = {**data, "tech_data": tech_data}

        base_score = self._calc_base_score(ticker, price, regime, atr)
        valid_results = await self._run_strategies(data)
        ensemble = self._ensemble(valid_results, tech_data)
        final_score, action = self._combine_scores(base_score, ensemble)

        # ── SQI 낮으면 HOLD 강제 (self.min_confidence 사용) ──────
        effective_sqi = ensemble.sqi_v2 if tech_data else ensemble.sqi
        if effective_sqi < self.min_confidence and action != Action.HOLD:
            trace.warning(
                f"SQI v2={effective_sqi:.2f} (v1={ensemble.sqi:.2f}) too low → forced HOLD",
                ticker=ticker,
            )
            action = Action.HOLD

        confidence = self._calc_signal_confidence(final_score, ensemble)
        positives, negatives = self._collect_evidence(
            action, final_score, ensemble, tech_data, regime
        )

        signal = Signal(
            ticker=ticker,
            action=action,
            score=final_score,
            confidence=confidence,
            price=price,
            entry_price=price if action.is_trade else None,
            atr=atr,
            positives=positives[:3],
            negatives=negatives[:2],
            trace_id=trace_id,
            timestamp=time.time(),
        )

        trace.debug(
            f"Signal generated: ticker={ticker} action={action.value} "
            f"score={final_score:.3f} sqi_v1={ensemble.sqi:.3f} "
            f"sqi_v2={ensemble.sqi_v2:.3f} consensus={ensemble.consensus:.2f}"
        )
        return signal

    # ═══════════════════════════════════════════════════════════════
    #  Private: 레거시 필터 스코어
    # ═══════════════════════════════════════════════════════════════

    def _calc_base_score(
        self, ticker: str, price: float, regime: str, atr: float
    ) -> float:
        """레거시 필터 가중 합산 스코어 계산."""
        macro_score = self.macro_filter.check({"price": price, "regime": regime})
        sector_score = self.sector_filter.check({"ticker": ticker})
        stock_score = self.stock_filter.check(
            {"ticker": ticker, "price": price, "regime": regime, "atr": atr}
        )
        korean_score = self.korean_filter.check({"ticker": ticker})
        weights = self.weighter.calculate({"regime": regime})

        score = (
            macro_score["score"] * weights.get("trend_weight", 0.3)
            + sector_score["score"] * weights.get("risk_weight", 0.2)
            + stock_score["score"] * weights.get("flow_weight", 0.4)
            + korean_score["score"] * 0.1
        )
        return max(0.0, min(1.0, score))

    # ═══════════════════════════════════════════════════════════════
    #  Private: 도메인 전략 실행
    # ═══════════════════════════════════════════════════════════════

    async def _run_strategies(
        self, data: Dict[str, Any]
    ) -> List[StrategyResult]:
        """도메인 전략을 asyncio.gather로 병렬 실행."""
        raw_results = await asyncio.gather(
            *[s.analyze(data) for s in self.strategies],
            return_exceptions=True,
        )

        valid: List[StrategyResult] = []
        for r in raw_results:
            if isinstance(r, Exception):
                logger.warning(f"Strategy execution error: {r}")
            elif isinstance(r, StrategyResult):
                valid.append(r)
        return valid

    # ═══════════════════════════════════════════════════════════════
    #  Private: 신뢰도 기반 앙상블
    # ═══════════════════════════════════════════════════════════════

    def _ensemble(
        self,
        results: List[StrategyResult],
        tech_data: Optional[Dict[str, Any]] = None,
    ) -> EnsembleResult:
        """신뢰도 기반 가중 앙상블 + 다수결 판정 + SQI v2 계산.

        v2.1: compute_sqi_v2() 호출 시 인스턴스 가중치
        (self.sqi_v2_momentum_w, sqi_v2_confidence_w, sqi_v2_consensus_w)를
        전달합니다. tech_data 없으면 sqi_v2 = sqi_v1 fallback.
        """
        if not results:
            return EnsembleResult(
                score=0.5, confidence=0.5,
                action="HOLD", consensus=0.0, sqi=0.0, sqi_v2=0.0, details=[]
            )

        strategy_map: Dict[str, Strategy] = {s.name: s for s in self.strategies}
        weighted_scores: Dict[str, float] = {}
        vote_weights: Dict[str, float] = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
        total_weight = 0.0
        details: List[str] = []

        for r in results:
            base_w = strategy_map.get(r.name, r).weight if r.name in strategy_map else 0.33
            combined_w = base_w * r.confidence
            weighted_scores[r.name] = combined_w
            total_weight += combined_w

            vote_action = r.action if r.action in vote_weights else "HOLD"
            vote_weights[vote_action] += combined_w

            details.append(f"{r.name}:{r.action}({r.score:.0%}/c{r.confidence:.0%})")

        if total_weight > 0:
            weighted_score_sum = sum(
                r.score * weighted_scores[r.name] for r in results
            )
            avg_score = weighted_score_sum / total_weight
            avg_confidence = sum(
                r.confidence * weighted_scores[r.name] for r in results
            ) / total_weight
        else:
            avg_score = 0.5
            avg_confidence = 0.5

        winning_action = max(vote_weights, key=lambda k: vote_weights[k])
        winning_weight = vote_weights[winning_action]
        consensus = winning_weight / total_weight if total_weight > 0 else 0.0

        if consensus < _MIN_CONSENSUS:
            winning_action = "HOLD"

        sqi = avg_confidence * consensus

        if tech_data:
            momentum_score = _calc_momentum_score(
                rsi=tech_data.get("rsi", 50.0),
                macd_hist=tech_data.get("macd_hist", 0.0),
            )
            volume_ratio = tech_data.get("volume_ratio", 1.0)
            price_ref = (
                tech_data.get("current_price") or tech_data.get("ema5", 0.0) or 0.0
            )
            bb_pct = _calc_bb_pct(
                price=price_ref,
                bb_upper=tech_data.get("bb_upper", 0.0),
                bb_lower=tech_data.get("bb_lower", 0.0),
            )
            sqi_v2 = compute_sqi_v2(
                momentum_score=momentum_score,
                volume_ratio=volume_ratio,
                bb_pct=bb_pct,
                confidence=avg_confidence,
                consensus=consensus,
                momentum_w=self.sqi_v2_momentum_w,       # 🆕 인스턴스 가중치
                confidence_w=self.sqi_v2_confidence_w,   # 🆕 인스턴스 가중치
                consensus_w=self.sqi_v2_consensus_w,     # 🆕 인스턴스 가중치
            )
        else:
            sqi_v2 = sqi

        return EnsembleResult(
            score=max(0.0, min(1.0, avg_score)),
            confidence=max(0.0, min(1.0, avg_confidence)),
            action=winning_action,
            consensus=consensus,
            sqi=sqi,
            sqi_v2=max(_SQI_V2_MIN, min(_SQI_V2_MAX, sqi_v2)),
            details=details,
        )

    # ═══════════════════════════════════════════════════════════════
    #  Private: 최종 스코어 결합 (v2.1: 인스턴스 임계값 사용)
    # ═══════════════════════════════════════════════════════════════

    def _combine_scores(
        self, base_score: float, ensemble: EnsembleResult
    ) -> Tuple[float, Action]:
        """레거시 필터 스코어와 앙상블 스코어를 결합해 최종 Action 결정."""
        final_score = (
            base_score * self.base_weight
            + ensemble.score * self.strategy_weight
            + 0.5 * self.momentum_weight
            + 0.5 * self.sentiment_weight
            + 0.5 * self.ml_weight
        )
        final_score = max(0.0, min(1.0, final_score))

        if final_score > self.buy_threshold:
            score_action = Action.BUY
        elif final_score < self.sell_threshold:
            score_action = Action.SELL
        else:
            score_action = Action.HOLD

        ensemble_action = Action(ensemble.action) if ensemble.action in (
            a.value for a in Action
        ) else Action.HOLD

        if score_action == ensemble_action:
            final_action = score_action
        elif ensemble_action == Action.HOLD:
            final_action = score_action
        else:
            final_action = Action.HOLD

        return final_score, final_action

    # ═══════════════════════════════════════════════════════════════
    #  Private: 신호 품질 계산
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _calc_signal_confidence(
        final_score: float, ensemble: EnsembleResult
    ) -> float:
        """Signal 최종 confidence 계산."""
        score_distance = abs(final_score - 0.5) * 2
        quality_index = ensemble.sqi_v2 if ensemble.sqi_v2 > 0 else ensemble.sqi
        raw = (score_distance * 0.6 + quality_index * 0.4)
        return max(0.30, min(0.95, raw))

    def _collect_evidence(
        self,
        action: Action,
        final_score: float,
        ensemble: EnsembleResult,
        tech_data: Dict[str, Any],
        regime: str,
    ) -> Tuple[List[str], List[str]]:
        """증거 수집 - positives / negatives 리스트 생성.

        v2.1: @staticmethod → 인스턴스 메서드로 전환.
        self.buy_threshold/self.sell_threshold를 참조하여
        실제 판단 기준(튜닝된 값)과 표시 근거가 항상 일치하도록 수정.
        """
        positives: List[str] = []
        negatives: List[str] = []

        display_sqi = ensemble.sqi_v2 if ensemble.sqi_v2 > 0 else ensemble.sqi
        sqi_label = "SQI_v2" if ensemble.sqi_v2 > 0 else "SQI_v1"

        if ensemble.details:
            positives.append(f"Ensemble: {' | '.join(ensemble.details)}")
        if display_sqi >= 0.5:
            positives.append(
                f"{sqi_label} {display_sqi:.2f} (합의도 {ensemble.consensus:.0%})"
            )
        if final_score > self.buy_threshold:
            positives.append(f"총점 {final_score:.1%}")
        if tech_data:
            rsi = tech_data.get("rsi", 50)
            positives.append(f"RSI {rsi:.0f} / Regime:{regime}")

        if display_sqi < 0.4:
            negatives.append(f"{sqi_label} 낮음 ({display_sqi:.2f}) - 전략 불일치")
        if final_score < self.sell_threshold:
            negatives.append(f"낮은 총점 ({final_score:.1%})")
        if ensemble.consensus < _MIN_CONSENSUS:
            negatives.append(f"전략 합의 미달 ({ensemble.consensus:.0%})")

        return positives, negatives

    # ═══════════════════════════════════════════════════════════════
    #  Private: OHLCV + 기술 지표 계산 (Bollinger Band, MACD 포함)
    # ═══════════════════════════════════════════════════════════════

    async def _fetch_ohlcv(self, ticker: str, period: int) -> Dict[str, Any]:
        """DB에서 OHLCV를 조회해 기술 지표를 계산합니다."""
        if not self.db_manager:
            return {}

        try:
            data = await self.db_manager.get_ohlcv(ticker, period)
            if len(data) < 5:
                return {}

            closes = [float(d["close"]) for d in data]
            highs = [float(d.get("high", d["close"])) for d in data]
            lows = [float(d.get("low", d["close"])) for d in data]
            volumes = [float(d.get("volume", 0)) for d in data if float(d.get("volume", 0)) > 0]

            ema5 = _ema(closes, 5)
            ema20 = _ema(closes, 20)
            ema60 = _ema(closes, min(60, len(closes)))

            rsi_val = _rsi(closes, 14)

            bb_upper, bb_middle, bb_lower = _bollinger_bands(closes, 20, 2.0)

            macd_line, macd_signal, macd_hist = _macd(closes, 12, 26, 9)

            avg_volume = sum(volumes) / len(volumes) if volumes else 1.0
            current_volume = volumes[-1] if volumes else 1.0
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

            return {
                "current_price": closes[-1],
                "high": highs[-1],
                "low": lows[-1],
                "volume_ratio": volume_ratio,
                "avg_volume": int(avg_volume),
                "ema5": ema5,
                "ema20": ema20,
                "ema60": ema60,
                "rsi": rsi_val,
                "bb_upper": bb_upper,
                "bb_middle": bb_middle,
                "bb_lower": bb_lower,
                "macd": macd_line,
                "macd_signal": macd_signal,
                "macd_hist": macd_hist,
            }
        except Exception as e:
            logger.debug(f"OHLCV fetch failed ({ticker}): {e}")
            return {}


# ═══════════════════════════════════════════════════════════════════
#  순수 기술 지표 계산 헬퍼 (모듈 수준 - 재사용 가능)
# ═══════════════════════════════════════════════════════════════════

def _ema(values: List[float], n: int) -> float:
    """지수이동평균(EMA) 계산."""
    if not values:
        return 0.0
    if len(values) < n:
        return values[-1]
    k = 2.0 / (n + 1)
    ema_val = values[0]
    for v in values[1:]:
        ema_val = v * k + ema_val * (1 - k)
    return ema_val


def _rsi(values: List[float], n: int = 14) -> float:
    """상대강도지수(RSI) 계산."""
    if len(values) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[-n:]) / n
    avg_loss = sum(losses[-n:]) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def _bollinger_bands(
    values: List[float], n: int = 20, k: float = 2.0
) -> Tuple[float, float, float]:
    """볼린저 밴드(Bollinger Bands) 계산."""
    if not values:
        return 0.0, 0.0, 0.0
    last = values[-1]
    if len(values) < n:
        return last * 1.05, last, last * 0.95

    window = values[-n:]
    mean = sum(window) / n
    variance = sum((v - mean) ** 2 for v in window) / n
    std = variance ** 0.5
    return mean + k * std, mean, mean - k * std


def _macd(
    values: List[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Tuple[float, float, float]:
    """MACD 계산."""
    if len(values) < slow + signal_period:
        return 0.0, 0.0, 0.0

    macd_history: List[float] = []
    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)

    ema_fast_val = values[0]
    ema_slow_val = values[0]

    for v in values[1:]:
        ema_fast_val = v * k_fast + ema_fast_val * (1 - k_fast)
        ema_slow_val = v * k_slow + ema_slow_val * (1 - k_slow)
        macd_history.append(ema_fast_val - ema_slow_val)

    if len(macd_history) < signal_period:
        return 0.0, 0.0, 0.0

    macd_line = macd_history[-1]
    signal_line = _ema(macd_history, signal_period)
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


# ═══════════════════════════════════════════════════════════════════
#  SQI v2 헬퍼 함수 (모듈 수준 - 재사용 가능)
# ═══════════════════════════════════════════════════════════════════

def _calc_momentum_score(rsi: float, macd_hist: float) -> float:
    """RSI + MACD 히스토그램 기반 모멘텀 품질 스코어 계산."""
    rsi_clamped = max(0.0, min(100.0, rsi))
    rsi_score = abs(rsi_clamped - 50.0) / 50.0

    if macd_hist > 0:
        macd_dir = 0.10
    elif macd_hist < 0:
        macd_dir = -0.10
    else:
        macd_dir = 0.0

    return max(0.0, min(1.0, rsi_score + macd_dir))


def _calc_bb_pct(
    price: float,
    bb_upper: float,
    bb_lower: float,
) -> float:
    """Bollinger %B 계산 (볼린저 밴드 내 현재 위치)."""
    band_width = bb_upper - bb_lower
    if band_width <= 0 or price <= 0:
        return 0.5
    pct_b = (price - bb_lower) / band_width
    return max(0.0, min(1.0, pct_b))


def compute_sqi_v2(
    momentum_score: float,
    volume_ratio: float,
    bb_pct: float,
    confidence: float,
    consensus: float,
    momentum_w: float = _SQI_V2_MOMENTUM_W,
    confidence_w: float = _SQI_V2_CONFIDENCE_W,
    consensus_w: float = _SQI_V2_CONSENSUS_W,
) -> float:
    """Signal Quality Index v2 계산.

    v2.1: momentum_w/confidence_w/consensus_w 선택적 인자 추가.
          기본값 = 모듈 상수이므로 기존 호출 코드와 완전 하위 호환.

    Args:
        momentum_score: RSI+MACD 기반 모멘텀 품질 (0~1)
        volume_ratio: 현재 거래량 / 평균 거래량 (0 이상, 1.0=기준)
        bb_pct: Bollinger %B 값 (0~1)
        confidence: 전략 가중 평균 신뢰도 (0~1)
        consensus: 다수결 합의도 (0~1)
        momentum_w: 모멘텀 가중치 (기본 0.30)
        confidence_w: 신뢰도 가중치 (기본 0.40)
        consensus_w: 합의도 가중치 (기본 0.30)

    Returns:
        float: SQI v2 값 (0~1)

    Examples:
        >>> round(compute_sqi_v2(0.6, 1.5, 0.5, 0.8, 0.9), 2)
        0.86
        >>> round(compute_sqi_v2(0.1, 0.3, 0.02, 0.3, 0.3), 2)
        0.16
    """
    base = (
        momentum_w * max(0.0, min(1.0, momentum_score))
        + confidence_w * max(0.0, min(1.0, confidence))
        + consensus_w * max(0.0, min(1.0, consensus))
    )

    safe_vol = max(volume_ratio, 0.01)
    volume_boost = 0.7 + 0.3 * math.log(safe_vol + 1.0)
    volume_boost = max(0.7, min(1.3, volume_boost))

    bb_pct_clamped = max(0.0, min(1.0, bb_pct))
    volatility_penalty = 1.0 - 0.4 * (abs(bb_pct_clamped - 0.5) * 2.0)

    sqi_v2 = base * volume_boost * volatility_penalty
    return max(_SQI_V2_MIN, min(_SQI_V2_MAX, sqi_v2))
