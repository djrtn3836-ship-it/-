# -*- coding: utf-8 -*-
"""
application/analysis/signal_pipeline.py - V10 Strategy Orchestration Pipeline v2.0

개선 사항 (v2.0):
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

# ─── 앙상블 상수 ───────────────────────────────────────────────────
_BUY_THRESHOLD = 0.62   # 최종 스코어 BUY 임계값
_SELL_THRESHOLD = 0.38  # 최종 스코어 SELL 임계값
_MIN_CONFIDENCE = 0.45  # 최소 신뢰도 (이하면 HOLD 강제)
_MIN_CONSENSUS = 0.50   # 다수결 합의도 임계값 (0.5 = 과반수)

# ─── SQI v2 가중치 상수 ──────────────────────────────────────────────
# sqi_v2 = (momentum_w × momentum_score + conf_w × confidence + cons_w × consensus)
#         × volume_boost × volatility_penalty
_SQI_V2_MOMENTUM_W = 0.30    # 모멘텀 스코어 가중치
_SQI_V2_CONFIDENCE_W = 0.40  # 신뢰도 가중치
_SQI_V2_CONSENSUS_W = 0.30   # 합의도 가중치
_SQI_V2_MIN = 0.0             # SQI v2 하한
_SQI_V2_MAX = 1.0             # SQI v2 상한


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

    Attributes:
        db_manager: 데이터베이스 관리자 (선택적)
        strategies: 도메인 전략 목록 (Trend, Reversal, Breakout)
        atr_service: ATR 계산 서비스
        base_weight: 레거시 필터 스코어 가중치
        strategy_weight: 도메인 전략 스코어 가중치
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

    def set_realtime_price_provider(self, provider: Callable[[str], float]) -> None:
        """실시간 가격 제공자 설정.

        Args:
            provider: ticker → 현재가 반환 함수
        """
        self._realtime_price_provider = provider
        if self.atr_service:
            self.atr_service.set_realtime_price_provider(provider)

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
        """Signal Quality Index v2 계산 (모듈 수준 함수 위임).

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
        # ── 1. 컨텍스트 추출 ─────────────────────────────────────
        ticker: str = data.get("ticker", "")
        price: float = float(data.get("price", 0))
        trace_id: str = data.get("trace_id", f"PIPE-{ticker}-{int(time.time()*1000)}")
        regime: str = data.get("regime", "Sideways")

        if price <= 0:
            return Signal.error(ticker, "Invalid price", trace_id)

        # ── 2. ATR 계산 ──────────────────────────────────────────
        atr = await self.atr_service.calculate(ticker, period=14)
        if atr == 0:
            atr = price * 0.01  # fallback: 1% of price
            trace.warning("ATR fallback to 1% of price", ticker=ticker, atr=atr)

        # ── 3. 기술 지표 데이터 확보 ─────────────────────────────
        tech_data: Dict[str, Any] = data.get("tech_data", {})
        if not tech_data and self.db_manager:
            tech_data = await self._fetch_ohlcv(ticker, 30)
        data = {**data, "tech_data": tech_data}  # 전략에 tech_data 전달

        # ── 4. 레거시 필터 base_score ─────────────────────────────
        base_score = self._calc_base_score(ticker, price, regime, atr)

        # ── 5. 도메인 전략 병렬 실행 ─────────────────────────────
        valid_results = await self._run_strategies(data)

        # ── 6. 신뢰도 기반 앙상블 (tech_data 전달 → SQI v2 계산) ─────
        ensemble = self._ensemble(valid_results, tech_data)

        # ── 7. 최종 스코어 결합 ──────────────────────────────────
        final_score, action = self._combine_scores(base_score, ensemble)

        # ── 8. 신호 품질 지수(SQI v2)가 낮으면 HOLD 강제 ──────────
        #    tech_data 있으면 sqi_v2, 없으면 sqi_v1 사용
        effective_sqi = ensemble.sqi_v2 if tech_data else ensemble.sqi
        if effective_sqi < _MIN_CONFIDENCE and action != Action.HOLD:
            trace.warning(
                f"SQI v2={effective_sqi:.2f} (v1={ensemble.sqi:.2f}) too low → forced HOLD",
                ticker=ticker,
            )
            action = Action.HOLD

        # ── 9. Signal 조립 ───────────────────────────────────────
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
        """레거시 필터 가중 합산 스코어 계산.

        Args:
            ticker: 종목 코드
            price: 현재가
            regime: 시장 레짐
            atr: ATR 값

        Returns:
            float: 0~1 범위의 base score
        """
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
        """도메인 전략을 asyncio.gather로 병렬 실행.

        Args:
            data: 전략에 전달할 시장 데이터

        Returns:
            List[StrategyResult]: 유효한 전략 결과 목록 (예외 제외)
        """
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
    #  Private: 신뢰도 기반 앙상블 ★ 핵심 개선
    # ═══════════════════════════════════════════════════════════════

    def _ensemble(
        self,
        results: List[StrategyResult],
        tech_data: Optional[Dict[str, Any]] = None,
    ) -> EnsembleResult:
        """신뢰도 기반 가중 앙상블 + 다수결 판정 + SQI v2 계산.

        기존 방식의 문제:
            - 고정 가중치 (Trend:0.4 등) → 신호 품질 무시
            - Strategy.weight 프로퍼티 미사용

        개선된 방식 (v3.0):
            - 각 전략의 weight × confidence를 결합 가중치로 사용
            - 다수결 투표 (BUY/SELL/HOLD 각각 가중치 합산)
            - consensus = 최다 득표 Action의 가중치 비율 (합의도)
            - SQI v1 = avg_confidence × consensus (기존)
            - SQI v2 = compute_sqi_v2(momentum, volume, bb_pct, confidence, consensus)
              → tech_data 없으면 sqi_v2 = sqi_v1 (fallback)

        Args:
            results: 유효한 전략 결과 목록
            tech_data: 기술 지표 딕셔너리 (SQI v2 계산에 사용, 선택적)

        Returns:
            EnsembleResult: 앙상블 집계 결과 (sqi_v2 포함)
        """
        if not results:
            return EnsembleResult(
                score=0.5, confidence=0.5,
                action="HOLD", consensus=0.0, sqi=0.0, sqi_v2=0.0, details=[]
            )

        # ── 가중치 계산: strategy.weight × result.confidence ─────
        strategy_map: Dict[str, Strategy] = {s.name: s for s in self.strategies}
        weighted_scores: Dict[str, float] = {}
        vote_weights: Dict[str, float] = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
        total_weight = 0.0
        details: List[str] = []

        for r in results:
            # 전략 고유 weight × 신뢰도 → 결합 가중치
            base_w = strategy_map.get(r.name, r).weight if r.name in strategy_map else 0.33
            combined_w = base_w * r.confidence
            weighted_scores[r.name] = combined_w
            total_weight += combined_w

            # 다수결 투표
            vote_action = r.action if r.action in vote_weights else "HOLD"
            vote_weights[vote_action] += combined_w

            details.append(f"{r.name}:{r.action}({r.score:.0%}/c{r.confidence:.0%})")

        # ── 가중 평균 스코어 ─────────────────────────────────────
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

        # ── 다수결 판정 ──────────────────────────────────────────
        winning_action = max(vote_weights, key=lambda k: vote_weights[k])
        winning_weight = vote_weights[winning_action]
        consensus = winning_weight / total_weight if total_weight > 0 else 0.0

        # 합의도가 너무 낮으면 HOLD
        if consensus < _MIN_CONSENSUS:
            winning_action = "HOLD"

        # ── Signal Quality Index v1 (기존) ───────────────────────
        sqi = avg_confidence * consensus

        # ── Signal Quality Index v2 (신규) ───────────────────────
        if tech_data:
            momentum_score = _calc_momentum_score(
                rsi=tech_data.get("rsi", 50.0),
                macd_hist=tech_data.get("macd_hist", 0.0),
            )
            volume_ratio = tech_data.get("volume_ratio", 1.0)
            # price 레이얼파이: current_price 우선, 없으면 ema5 fallback
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
            )
        else:
            # tech_data 없으면 SQI v1과 동일값으로 fallback
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
    #  Private: 최종 스코어 결합
    # ═══════════════════════════════════════════════════════════════

    def _combine_scores(
        self, base_score: float, ensemble: EnsembleResult
    ) -> Tuple[float, Action]:
        """레거시 필터 스코어와 앙상블 스코어를 결합해 최종 Action 결정.

        Args:
            base_score: 레거시 필터 가중 합산 스코어 (0~1)
            ensemble: 도메인 전략 앙상블 결과

        Returns:
            Tuple[float, Action]: (최종 스코어, 최종 Action)
        """
        final_score = (
            base_score * self.base_weight
            + ensemble.score * self.strategy_weight
            + 0.5 * self.momentum_weight
            + 0.5 * self.sentiment_weight
            + 0.5 * self.ml_weight
        )
        final_score = max(0.0, min(1.0, final_score))

        # 스코어 임계값 기반 Action
        if final_score > _BUY_THRESHOLD:
            score_action = Action.BUY
        elif final_score < _SELL_THRESHOLD:
            score_action = Action.SELL
        else:
            score_action = Action.HOLD

        # 앙상블 다수결 Action과 스코어 Action의 일치 여부로 최종 결정
        ensemble_action = Action(ensemble.action) if ensemble.action in (
            a.value for a in Action
        ) else Action.HOLD

        if score_action == ensemble_action:
            # 완전 일치 → 그대로 채택
            final_action = score_action
        elif ensemble_action == Action.HOLD:
            # 앙상블이 HOLD → 스코어 따름
            final_action = score_action
        else:
            # 불일치 → 더 보수적인 선택 (HOLD)
            final_action = Action.HOLD

        return final_score, final_action

    # ═══════════════════════════════════════════════════════════════
    #  Private: 신호 품질 계산
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _calc_signal_confidence(
        final_score: float, ensemble: EnsembleResult
    ) -> float:
        """Signal 최종 confidence 계산.

        스코어 거리 + SQI를 결합해 신뢰도를 산정합니다.

        Args:
            final_score: 최종 결합 스코어 (0~1)
            ensemble: 앙상블 결과

        Returns:
            float: 0.3~0.95 범위의 최종 confidence
        """
        score_distance = abs(final_score - 0.5) * 2  # 0~1 정규화
        # sqi_v2가 0이면 (tech_data 없음) sqi_v1 사용, 아니면 sqi_v2 사용
        quality_index = ensemble.sqi_v2 if ensemble.sqi_v2 > 0 else ensemble.sqi
        raw = (score_distance * 0.6 + quality_index * 0.4)
        return max(0.30, min(0.95, raw))

    @staticmethod
    def _collect_evidence(
        action: Action,
        final_score: float,
        ensemble: EnsembleResult,
        tech_data: Dict[str, Any],
        regime: str,
    ) -> Tuple[List[str], List[str]]:
        """증거 수집 - positives / negatives 리스트 생성.

        SQI v2가 있으면 우선 사용, 없으면 SQI v1으로 fallback.

        Args:
            action: 최종 Action
            final_score: 최종 스코어
            ensemble: 앙상블 결과 (sqi_v2 포함)
            tech_data: 기술 지표 데이터
            regime: 시장 레짐

        Returns:
            Tuple[List[str], List[str]]: (긍정 근거, 부정 근거)
        """
        positives: List[str] = []
        negatives: List[str] = []

        # SQI v2 우선 사용, 없으면 SQI v1 fallback
        display_sqi = ensemble.sqi_v2 if ensemble.sqi_v2 > 0 else ensemble.sqi
        sqi_label = "SQI_v2" if ensemble.sqi_v2 > 0 else "SQI_v1"

        if ensemble.details:
            positives.append(f"Ensemble: {' | '.join(ensemble.details)}")
        if display_sqi >= 0.5:
            positives.append(
                f"{sqi_label} {display_sqi:.2f} (합의도 {ensemble.consensus:.0%})"
            )
        if final_score > _BUY_THRESHOLD:
            positives.append(f"총점 {final_score:.1%}")
        if tech_data:
            rsi = tech_data.get("rsi", 50)
            positives.append(f"RSI {rsi:.0f} / Regime:{regime}")

        if display_sqi < 0.4:
            negatives.append(f"{sqi_label} 낮음 ({display_sqi:.2f}) - 전략 불일치")
        if final_score < _SELL_THRESHOLD:
            negatives.append(f"낮은 총점 ({final_score:.1%})")
        if ensemble.consensus < _MIN_CONSENSUS:
            negatives.append(f"전략 합의 미달 ({ensemble.consensus:.0%})")

        return positives, negatives

    # ═══════════════════════════════════════════════════════════════
    #  Private: OHLCV + 기술 지표 계산 (Bollinger Band, MACD 추가)
    # ═══════════════════════════════════════════════════════════════

    async def _fetch_ohlcv(self, ticker: str, period: int) -> Dict[str, Any]:
        """DB에서 OHLCV를 조회해 기술 지표를 계산합니다.

        계산 지표:
            - EMA 5, 20, 60
            - RSI 14
            - Bollinger Bands (20일, 2σ) ← 신규
            - MACD (12-26-9) ← 신규
            - Volume Ratio (현재 / 평균)

        Args:
            ticker: 종목 코드
            period: 조회 기간 (영업일 수)

        Returns:
            Dict[str, Any]: 기술 지표 딕셔너리 (데이터 부족 시 빈 dict)
        """
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

            # ── EMA ───────────────────────────────────────────────
            ema5 = _ema(closes, 5)
            ema20 = _ema(closes, 20)
            ema60 = _ema(closes, min(60, len(closes)))

            # ── RSI ───────────────────────────────────────────────
            rsi_val = _rsi(closes, 14)

            # ── Bollinger Bands (20일, 2σ) ─────────────────────
            bb_upper, bb_middle, bb_lower = _bollinger_bands(closes, 20, 2.0)

            # ── MACD (12-26-9) ────────────────────────────────
            macd_line, macd_signal, macd_hist = _macd(closes, 12, 26, 9)

            # ── Volume ────────────────────────────────────────────
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
    """지수이동평균(EMA) 계산.

    Args:
        values: 종가 리스트
        n: 기간

    Returns:
        float: 최신 EMA 값 (데이터 부족 시 마지막 값 반환)
    """
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
    """상대강도지수(RSI) 계산.

    Args:
        values: 종가 리스트
        n: 기간 (기본 14)

    Returns:
        float: RSI (0~100), 데이터 부족 시 50 반환
    """
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
    """볼린저 밴드(Bollinger Bands) 계산.

    Args:
        values: 종가 리스트
        n: 이동평균 기간 (기본 20)
        k: 표준편차 배수 (기본 2.0)

    Returns:
        Tuple[float, float, float]: (upper, middle, lower)
            데이터 부족 시 last_price ± 5% 반환
    """
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
    """MACD (Moving Average Convergence/Divergence) 계산.

    Args:
        values: 종가 리스트
        fast: 단기 EMA 기간 (기본 12)
        slow: 장기 EMA 기간 (기본 26)
        signal_period: Signal EMA 기간 (기본 9)

    Returns:
        Tuple[float, float, float]: (macd_line, signal_line, histogram)
            데이터 부족 시 (0.0, 0.0, 0.0) 반환
    """
    if len(values) < slow + signal_period:
        return 0.0, 0.0, 0.0

    # 각 시점의 MACD 라인 계산 (signal용 히스토리 필요)
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


# ═══════════════════════════════════════════════════════════════════════
#  SQI v2 헬퍼 함수 (모듈 수준 - 재사용 가능)
# ═══════════════════════════════════════════════════════════════════════

def _calc_momentum_score(rsi: float, macd_hist: float) -> float:
    """RSI + MACD 히스토그램 기반 모멘텀 품질 스코어 계산.

    RSI 정규화 (중앙 50 거리 기반):
        - RSI < 30 / RSI > 70: 극단 → 높은 모멘텀 품질
        - RSI 40~60: 중립 → 낮은 모멘텀 품질
        공식: abs(rsi - 50) / 50 → [0, 1]

    MACD 방향 보정 (±0.1):
        - macd_hist > 0 → +0.1 (상승 모멘텀)
        - macd_hist < 0 → -0.1 (하락 모멘텀)
        - macd_hist == 0 → 보정 없음

    최종: clamp(rsi_score + macd_dir, 0.0, 1.0)

    Args:
        rsi: RSI 값 (0~100)
        macd_hist: MACD 히스토그램 값 (양수=상승, 음수=하락)

    Returns:
        float: 모멘텀 품질 스코어 (0~1)
    """
    rsi_clamped = max(0.0, min(100.0, rsi))
    rsi_score = abs(rsi_clamped - 50.0) / 50.0  # [0, 1]

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
    """Bollinger %B 계산 (볼린저 밴드 내 현재 위치).

    공식: %B = (price - lower) / (upper - lower)
        - 0.0 → 하단 밴드 (과매도)
        - 0.5 → 중간선 (중립)
        - 1.0 → 상단 밴드 (과매수)

    Args:
        price: 현재가
        bb_upper: 볼린저 상단 밴드
        bb_lower: 볼린저 하단 밴드

    Returns:
        float: Bollinger %B (0~1, 범위 초과 시 clamp)
    """
    band_width = bb_upper - bb_lower
    if band_width <= 0 or price <= 0:
        return 0.5  # 데이터 부족 → 중립
    pct_b = (price - bb_lower) / band_width
    return max(0.0, min(1.0, pct_b))


def compute_sqi_v2(
    momentum_score: float,
    volume_ratio: float,
    bb_pct: float,
    confidence: float,
    consensus: float,
) -> float:
    """Signal Quality Index v2 계산.

    기존 SQI v1 = confidence × consensus 에서
    모멘텀·거래량·변동성 3개 차원을 추가한 복합 스코어.

    공식:
        base = (momentum_w × momentum_score
               + conf_w × confidence
               + cons_w × consensus)
        volume_boost  = clamp(0.7 + 0.3 × ln(max(volume_ratio, 0.01) + 1), 0.7, 1.3)
        volatility_pn = 1.0 - 0.4 × |bb_pct - 0.5| × 2  → [0.6, 1.0]
                        (bb_pct=0.5 → 패널티 없음, 0 or 1 → 최대 패널티 0.4)
        sqi_v2 = clamp(base × volume_boost × volatility_pn, 0.0, 1.0)

    Args:
        momentum_score: RSI+MACD 기반 모멘텀 품질 (0~1)
        volume_ratio: 현재 거래량 / 평균 거래량 (0 이상, 1.0=기준)
        bb_pct: Bollinger %B 값 (0~1)
        confidence: 전략 가중 평균 신뢰도 (0~1)
        consensus: 다수결 합의도 (0~1)

    Returns:
        float: SQI v2 값 (0~1)

    Examples:
        >>> round(compute_sqi_v2(0.6, 1.5, 0.5, 0.8, 0.9), 2)
        0.86
        >>> round(compute_sqi_v2(0.1, 0.3, 0.02, 0.3, 0.3), 2)
        0.16
    """
    import math

    # ── 1. 기본 가중합 ───────────────────────────────────────────
    base = (
        _SQI_V2_MOMENTUM_W * max(0.0, min(1.0, momentum_score))
        + _SQI_V2_CONFIDENCE_W * max(0.0, min(1.0, confidence))
        + _SQI_V2_CONSENSUS_W * max(0.0, min(1.0, consensus))
    )

    # ── 2. 거래량 부스트 (로그 스케일) ──────────────────────────
    # volume_ratio=1.0 → boost≈1.0, =2.0 → boost≈1.1, =0.5 → boost≈0.86
    safe_vol = max(volume_ratio, 0.01)
    volume_boost = 0.7 + 0.3 * math.log(safe_vol + 1.0)
    volume_boost = max(0.7, min(1.3, volume_boost))  # [0.7, 1.3]

    # ── 3. 변동성 패널티 (BB %B 기반) ───────────────────────────
    # bb_pct=0.5 → penalty=0 (중립, 패널티 없음)
    # bb_pct=0 or 1 → 최대 패널티 0.4
    bb_pct_clamped = max(0.0, min(1.0, bb_pct))
    volatility_penalty = 1.0 - 0.4 * (abs(bb_pct_clamped - 0.5) * 2.0)
    # 범위: [0.6, 1.0]

    # ── 4. 최종 SQI v2 ──────────────────────────────────────────
    sqi_v2 = base * volume_boost * volatility_penalty
    return max(_SQI_V2_MIN, min(_SQI_V2_MAX, sqi_v2))
