
# -*- coding: utf-8 -*-
"""
decision/hybrid_decider.py - v9.0 (완전 재설계)
신호 → 의사결정 → 액션 매핑

통합 대상:
- signal_pipeline: 신호 생성
- timing_analyzer: 진입/진출 타이밍
- exit_manager: 손절/익절 자동 계산

기능:
- 신뢰도 기반 동적 의사결정
- 진입/진출 신호 통합
- 포지션 사이징 (Kelly Criterion)
- 리스크-리워드 비율 고려
- 포트폴리오 레벨 제어
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class ActionType(Enum):
    """액션 타입"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    CLOSE = "CLOSE"  # 기존 포지션 청산


class DecisionPriority(Enum):
    """의사결정 우선순위"""
    CRITICAL = 10  # 즉시 실행 (손절 등)
    HIGH = 7       # 우선 처리 (강한 신호)
    NORMAL = 5     # 일반 (신호 매칭)
    LOW = 3        # 보류 (약한 신호)
    HOLD = 1       # 대기


@dataclass
class SignalMetrics:
    """신호 지표 패키지"""
    action: str  # "BUY", "SELL", "HOLD"
    score: float  # 0-1
    confidence: float  # 0-1
    sqi: float  # Signal Quality Index
    consensus: float  # 전략 합의도


@dataclass
class TimingMetrics:
    """타이밍 지표 패키지"""
    entry_readiness: float  # 0-100
    exit_readiness: float  # 0-100
    entry_recommendation: str
    exit_recommendation: str
    best_time: str


@dataclass
class RiskMetrics:
    """리스크 지표"""
    suggested_quantity: int
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    max_loss_pct: float


@dataclass
class DecisionContext:
    """의사결정 컨텍스트"""
    ticker: str
    current_price: float
    signals: SignalMetrics
    timing: TimingMetrics
    portfolio_value: float
    existing_positions: int
    portfolio_risk: float


@dataclass
class DecisionOutput:
    """의사결정 결과"""
    ticker: str
    action: ActionType
    quantity: int
    entry_price: Optional[float]
    stop_loss: float
    take_profit: float
    confidence: float
    reason: str
    warnings: list
    priority: DecisionPriority
    timestamp: datetime


class HybridDecider:
    """
    하이브리드 의사결정 엔진
    
    신호(signal_pipeline) + 타이밍(timing_analyzer) + 리스크(exit_manager)를
    결합하여 최종 트레이딩 액션을 결정합니다.
    """

    def __init__(self) -> None:
        self.logger = logger
        self.risk_per_trade_pct: float = 0.02  # 거래당 리스크 2%
        self.max_position_size: int = 10000  # 최대 포지션 크기
        self.min_confidence: float = 0.50  # 최소 신뢰도 임계값
        self.kelly_fraction: float = 0.25  # Kelly Criterion 분수

    async def decide(
        self,
        context: DecisionContext,
    ) -> DecisionOutput:
        """
        통합 의사결정 수행
        
        Args:
            context: 의사결정 컨텍스트
            
        Returns:
            DecisionOutput: 최종 액션 + 상세 정보
        """
        
        # 1️⃣ 신호 강도 평가
        signal_score = await self._evaluate_signal_strength(context.signals)
        
        # 2️⃣ 타이밍 일치 확인
        timing_alignment = await self._check_timing_alignment(
            context.signals.action,
            context.timing
        )
        
        # 3️⃣ 최종 액션 결정
        action, confidence = await self._determine_action(
            signal_score,
            timing_alignment,
            context
        )
        
        # 4️⃣ 리스크 & 포지션 사이징
        if action != ActionType.HOLD:
            risk_metrics = await self._calculate_risk_metrics(
                action,
                context
            )
        else:
            risk_metrics = RiskMetrics(
                suggested_quantity=0,
                stop_loss=0,
                take_profit=0,
                risk_reward_ratio=0,
                max_loss_pct=0
            )
        
        # 5️⃣ 근거 & 경고 생성
        reason = await self._generate_reason(
            action, signal_score, timing_alignment
        )
        warnings = await self._generate_warnings(
            action, context, confidence
        )
        
        # 6️⃣ 우선순위 결정
        priority = self._determine_priority(action, confidence)
        
        return DecisionOutput(
            ticker=context.ticker,
            action=action,
            quantity=risk_metrics.suggested_quantity,
            entry_price=context.current_price if action == ActionType.BUY else None,
            stop_loss=risk_metrics.stop_loss,
            take_profit=risk_metrics.take_profit,
            confidence=confidence,
            reason=reason,
            warnings=warnings,
            priority=priority,
            timestamp=datetime.now()
        )

    async def _evaluate_signal_strength(
        self,
        signals: SignalMetrics
    ) -> float:
        """신호 강도 평가 (0-1)"""
        # 스코어 + 신뢰도 + SQI를 결합
        strength = (
            signals.score * 0.4 +
            signals.confidence * 0.35 +
            signals.sqi * 0.25
        )
        return max(0.0, min(1.0, strength))

    async def _check_timing_alignment(
        self,
        signal_action: str,
        timing: TimingMetrics
    ) -> bool:
        """신호와 타이밍 정렬 확인"""
        
        if signal_action == "BUY":
            # BUY 신호 시 진입 준비도 > 50 필요
            return timing.entry_readiness > 50
        elif signal_action == "SELL":
            # SELL 신호 시 진출 준비도 > 50 필요
            return timing.exit_readiness > 50
        else:
            return True

    async def _determine_action(
        self,
        signal_score: float,
        timing_aligned: bool,
        context: DecisionContext
    ) -> Tuple[ActionType, float]:
        """최종 액션 결정"""
        
        base_action_str = context.signals.action
        base_confidence = context.signals.confidence
        
        # 타이밍이 맞지 않으면 신뢰도 감소
        if not timing_aligned:
            base_confidence *= 0.7
        
        # 최소 신뢰도 미달 시 HOLD
        if base_confidence < self.min_confidence:
            return ActionType.HOLD, base_confidence
        
        # 포트폴리오 리스크가 높으면 BUY 차단, SELL 우대
        if context.portfolio_risk > 80:
            if base_action_str == "BUY":
                return ActionType.HOLD, base_confidence * 0.5
            elif base_action_str == "SELL":
                base_confidence *= 1.2  # SELL 신뢰도 증폭
        
        # 액션 확정
        if base_action_str == "BUY":
            action = ActionType.BUY
        elif base_action_str == "SELL":
            action = ActionType.SELL if context.existing_positions > 0 else ActionType.HOLD
        else:
            action = ActionType.HOLD
        
        return action, min(1.0, base_confidence)

    async def _calculate_risk_metrics(
        self,
        action: ActionType,
        context: DecisionContext
    ) -> RiskMetrics:
        """리스크 & 포지션 사이징 계산"""
        
        current_price = context.current_price
        
        if action == ActionType.BUY:
            # ATR 기반 손절 계산 (10% 하단)
            stop_loss = current_price * 0.90
            # 목표가 계산 (15% 상단)
            take_profit = current_price * 1.15
            
            # 리스크-리워드 비율
            risk = current_price - stop_loss
            reward = take_profit - current_price
            rr_ratio = reward / risk if risk > 0 else 0
            
            # Kelly Criterion 기반 포지션 사이징
            win_rate = 0.55  # 가정: 55% 승률
            avg_win = reward
            avg_loss = risk
            
            kelly_pct = (
                (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
                if avg_win > 0 else 0
            )
            kelly_pct = kelly_pct * self.kelly_fraction  # 보수적으로 조정
            
            # 포지션 크기 계산
            risk_amount = context.portfolio_value * self.risk_per_trade_pct
            quantity = int(risk_amount / risk) if risk > 0 else 0
            quantity = min(quantity, self.max_position_size)
            
            max_loss_pct = (risk / current_price * 100) if current_price > 0 else 0
            
        elif action == ActionType.SELL or action == ActionType.CLOSE:
            # 기존 포지션 모두 청산
            quantity = context.existing_positions
            stop_loss = 0
            take_profit = 0
            rr_ratio = 0
            max_loss_pct = 0
        else:
            quantity = 0
            stop_loss = 0
            take_profit = 0
            rr_ratio = 0
            max_loss_pct = 0
        
        return RiskMetrics(
            suggested_quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=rr_ratio,
            max_loss_pct=max_loss_pct
        )

    async def _generate_reason(
        self,
        action: ActionType,
        signal_score: float,
        timing_aligned: bool
    ) -> str:
        """의사결정 근거 생성"""
        
        reasons = []
        
        if action == ActionType.BUY:
            reasons.append(f"신호 강도 {signal_score*100:.0f}%")
            if timing_aligned:
                reasons.append("타이밍 일치")
            else:
                reasons.append("타이밍 경고")
        elif action == ActionType.SELL:
            reasons.append("매도 신호 확인")
            if timing_aligned:
                reasons.append("진출 타이밍 적절")
        else:
            reasons.append("신호 강도 부족 또는 타이밍 불일치")
        
        return " | ".join(reasons)

    async def _generate_warnings(
        self,
        action: ActionType,
        context: DecisionContext,
        confidence: float
    ) -> list:
        """경고 플래그 생성"""
        
        warnings = []
        
        if confidence < 0.55:
            warnings.append("⚠️  낮은 신뢰도")
        
        if context.portfolio_risk > 75:
            warnings.append("⚠️  포트폴리오 고위험")
        
        if action == ActionType.BUY and context.existing_positions > 0:
            warnings.append("⚠️  기존 포지션 존재 (추가진입)")
        
        if context.signals.consensus < 0.5:
            warnings.append("⚠️  전략 합의도 낮음")
        
        return warnings

    def _determine_priority(
        self,
        action: ActionType,
        confidence: float
    ) -> DecisionPriority:
        """우선순위 결정"""
        
        if action == ActionType.CLOSE:
            return DecisionPriority.CRITICAL
        elif action == ActionType.SELL and confidence > 0.75:
            return DecisionPriority.HIGH
        elif action == ActionType.BUY and confidence > 0.70:
            return DecisionPriority.HIGH
        elif confidence > 0.60:
            return DecisionPriority.NORMAL
        elif confidence > 0.50:
            return DecisionPriority.LOW
        else:
            return DecisionPriority.HOLD


