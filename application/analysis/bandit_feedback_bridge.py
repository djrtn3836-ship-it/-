# -*- coding: utf-8 -*-
"""
application/analysis/bandit_feedback_bridge.py - StrategyBandit ↔ PerformanceTracker 피드백 브리지 v1.0

목적:
    PerformanceTracker의 성과 갱신 사이클이 끝나면,
    전략별 실현 수익률을 StrategyBandit에 자동으로 피드백합니다.

    이로써 수익률이 높았던 전략의 선택 확률이 자동으로 올라가고,
    손실이 컸던 전략은 낮아집니다 (Thompson Sampling 자동 최적화).

아키텍처:
    PerformanceTracker._update_metrics()
        └─(hook)─► BanditFeedbackBridge.on_performance_updated()
                        └─► DB.get_strategy_outcomes()
                        └─► _compute_strategy_rewards()
                        └─► StrategyBandit.bulk_update(outcomes)
                        └─► DeepAnalyzer.update_strategy_weights(weights)

설계 원칙:
    - 비동기 안전: asyncio.Lock 보호
    - 폴백 안전: DB 없거나 전략 매핑 실패 시 기존 가중치 유지
    - 보상 정규화: return_1d → reward (SQI 보정 포함)
    - 순수 비즈니스 로직: observability 트레이서 선택적 사용
    - 5분 최소 간격: PerformanceTracker와 동일한 주기로 throttle

보상 계산 공식:
    reward = return_1d × confidence_factor
    confidence_factor = sqi if sqi > 0 else 1.0
    (SQI가 없는 구 결정은 return_1d를 그대로 사용)
"""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from data.db_manager import DatabaseManager
    from application.analysis.strategy_bandit import StrategyBandit

logger = logging.getLogger(__name__)

# 전략 이름 정규화 테이블 (DB strategy_scores 키 → Bandit arm 이름)
_STRATEGY_NAME_MAP: Dict[str, str] = {
    "trend": "Trend",
    "Trend": "Trend",
    "TrendStrategy": "Trend",
    "reversal": "Reversal",
    "Reversal": "Reversal",
    "ReversalStrategy": "Reversal",
    "breakout": "Breakout",
    "Breakout": "Breakout",
    "BreakoutStrategy": "Breakout",
}

# 보상 클리핑 범위 (너무 큰 단일 결과가 Bandit을 왜곡하는 것 방지)
_REWARD_CLIP_MIN = -0.10   # 최소 보상 (-10%)
_REWARD_CLIP_MAX = +0.10   # 최대 보상 (+10%)

# 최소 피드백 간격 (초)
_MIN_FEEDBACK_INTERVAL_SEC = 60


def _normalize_strategy_name(raw: str) -> Optional[str]:
    """전략 이름을 Bandit arm 이름으로 정규화."""
    return _STRATEGY_NAME_MAP.get(raw)


def _clip_reward(r: float) -> float:
    """보상을 [-10%, +10%] 범위로 클리핑."""
    return max(_REWARD_CLIP_MIN, min(_REWARD_CLIP_MAX, r))


class BanditFeedbackBridge:
    """StrategyBandit ↔ PerformanceTracker 실시간 피드백 브리지.

    사용 예::

        bridge = BanditFeedbackBridge(db=db_manager, bandit=strategy_bandit)

        # PerformanceTracker._update_metrics() 완료 후 호출
        await bridge.on_performance_updated()

        # 현재 최적 가중치 조회 (DeepAnalyzer 반영용)
        weights = bridge.get_current_weights()
    """

    def __init__(
        self,
        db: "DatabaseManager",
        bandit: "StrategyBandit",
        feedback_days: int = 7,
    ) -> None:
        """
        Args:
            db: DB 매니저 (get_strategy_outcomes 사용)
            bandit: 피드백을 받을 StrategyBandit 인스턴스
            feedback_days: 피드백 수집 기간 (기본 7일 — 최신 결과 위주)
        """
        self._db = db
        self._bandit = bandit
        self._feedback_days = feedback_days
        self._lock = asyncio.Lock()
        self._last_feedback: Optional[datetime] = None
        self._total_feedbacks: int = 0
        self._last_outcome_count: int = 0

    # ─── 외부 진입점 ────────────────────────────────────────────────

    async def on_performance_updated(self) -> Dict[str, float]:
        """PerformanceTracker 갱신 완료 후 호출되는 훅.

        DB에서 전략별 최근 결과를 조회하고 StrategyBandit을 업데이트합니다.

        Returns:
            dict: 갱신 후 Bandit 가중치 {전략이름: 가중치}
                  갱신 없으면 현재 가중치 반환
        """
        # throttle: 최소 간격 체크
        if self._last_feedback is not None:
            elapsed = (datetime.now() - self._last_feedback).total_seconds()
            if elapsed < _MIN_FEEDBACK_INTERVAL_SEC:
                return self._bandit.get_weights()

        async with self._lock:
            try:
                outcomes = await self._db.get_strategy_outcomes(days=self._feedback_days)
                if not outcomes:
                    logger.debug("BanditBridge: outcome 없음 (기간=%dd)", self._feedback_days)
                    return self._bandit.get_weights()

                rewards = self._compute_strategy_rewards(outcomes)
                if not rewards:
                    logger.debug("BanditBridge: 전략 매핑 실패 (outcome=%d건)", len(outcomes))
                    return self._bandit.get_weights()

                await self._bandit.bulk_update(rewards)

                self._last_feedback = datetime.now()
                self._total_feedbacks += 1
                self._last_outcome_count = len(outcomes)

                weights = self._bandit.get_weights()
                logger.info(
                    "BanditBridge: 피드백 완료 (outcome=%d건, strategies=%s, weights=%s)",
                    len(outcomes),
                    list(rewards.keys()),
                    {k: round(v, 3) for k, v in weights.items()},
                )
                return weights

            except Exception as e:
                logger.error("BanditBridge: 피드백 실패 %s", e, exc_info=True)
                return self._bandit.get_weights()

    async def force_feedback(self, days: Optional[int] = None) -> Dict[str, float]:
        """throttle 무시하고 즉시 피드백 강제 실행 (수동 트리거용).

        Args:
            days: 강제 피드백 기간 (None이면 기본값 사용)

        Returns:
            dict: 갱신 후 Bandit 가중치
        """
        old_last = self._last_feedback
        self._last_feedback = None   # throttle 우회
        days_to_use = days or self._feedback_days
        old_days = self._feedback_days
        self._feedback_days = days_to_use
        try:
            return await self.on_performance_updated()
        finally:
            self._feedback_days = old_days
            if self._last_feedback is None:
                self._last_feedback = old_last   # 실패 시 복원

    # ─── 핵심 보상 계산 ────────────────────────────────────────────

    def _compute_strategy_rewards(
        self, outcomes: List[dict]
    ) -> Dict[str, float]:
        """전략별 평균 보상 계산.

        알고리즘:
            1. 각 decision의 strategy_scores에서 활성 전략 파악
            2. 실현 수익률(return_1d)을 전략 점수 비율로 배분
            3. 전략별 누적 보상을 평균 → reward 클리핑 → bulk_update

        보상 배분 로직:
            - strategy_scores = {"Trend": 0.72, "Reversal": 0.41}
            - 가장 높은 점수의 전략이 주도한 것으로 간주
            - 주도 전략: return_1d × 1.0
            - 보조 전략 (점수 0.5 이상): return_1d × 0.3
            - 나머지: 보상 없음 (신호 노이즈 제거)

        Args:
            outcomes: DB.get_strategy_outcomes() 반환값

        Returns:
            dict: {전략이름: 평균 보상}  (빈 dict = 매핑 실패)
        """
        strategy_rewards: Dict[str, List[float]] = {}

        for outcome in outcomes:
            ret = outcome.get("return_1d")
            if ret is None:
                continue
            ret = float(ret)

            strategy_scores = outcome.get("strategy_scores", {})
            # "scores" 키 안에 전략 점수가 있는 경우 처리
            if "scores" in strategy_scores:
                strategy_scores = strategy_scores["scores"]

            if not isinstance(strategy_scores, dict) or not strategy_scores:
                continue

            # 전략 이름 정규화
            normalized: Dict[str, float] = {}
            for raw_name, score in strategy_scores.items():
                norm = _normalize_strategy_name(str(raw_name))
                if norm and isinstance(score, (int, float)):
                    normalized[norm] = float(score)

            if not normalized:
                continue

            # 주도 전략 파악 (최고 점수)
            leader = max(normalized, key=lambda k: normalized[k])
            leader_score = normalized[leader]

            for name, score in normalized.items():
                if name == leader:
                    # 주도 전략: 실현 수익률 전액 배분
                    reward = _clip_reward(ret)
                elif score >= 0.5 and leader_score > 0:
                    # 보조 전략 (점수 0.5 이상): 비율 배분
                    ratio = score / leader_score * 0.3
                    reward = _clip_reward(ret * ratio)
                else:
                    continue   # 기여도 미미 → 피드백 생략

                strategy_rewards.setdefault(name, []).append(reward)

        # 전략별 평균 보상 계산
        result: Dict[str, float] = {}
        for name, rewards in strategy_rewards.items():
            if rewards:
                avg = sum(rewards) / len(rewards)
                result[name] = round(avg, 6)

        return result

    # ─── 상태 조회 ──────────────────────────────────────────────────

    def get_current_weights(self) -> Dict[str, float]:
        """현재 Bandit 가중치 반환 (DeepAnalyzer 반영용)."""
        return self._bandit.get_weights()

    def get_bandit_stats(self) -> List[dict]:
        """전략별 상세 통계 반환."""
        return self._bandit.get_stats()

    def get_status(self) -> dict:
        """브리지 상태 반환 (헬스체크용)."""
        return {
            "last_feedback": self._last_feedback.isoformat() if self._last_feedback else None,
            "total_feedbacks": self._total_feedbacks,
            "last_outcome_count": self._last_outcome_count,
            "feedback_days": self._feedback_days,
            "bandit_weights": self.get_current_weights(),
            "bandit_stats": self.get_bandit_stats(),
        }

    def get_recommended_strategy(self) -> tuple:
        """현재 최우선 전략 반환 (탐욕적)."""
        return self._bandit.get_recommended_strategy()

    def next_feedback_in_seconds(self) -> float:
        """다음 피드백까지 남은 초 (0이면 즉시 가능)."""
        if self._last_feedback is None:
            return 0.0
        elapsed = (datetime.now() - self._last_feedback).total_seconds()
        remaining = _MIN_FEEDBACK_INTERVAL_SEC - elapsed
        return max(0.0, remaining)
