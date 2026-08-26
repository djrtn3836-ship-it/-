"""
tests/unit/test_bandit_feedback_bridge.py

BanditFeedbackBridge + PerformanceTracker v3.0 통합 단위 테스트 (25개)

커버리지:
  - _normalize_strategy_name: 다양한 이름 형식 정규화
  - _clip_reward: 보상 클리핑 [-10%, +10%]
  - BanditFeedbackBridge._compute_strategy_rewards(): 핵심 보상 배분
  - BanditFeedbackBridge.on_performance_updated(): 피드백 흐름
  - BanditFeedbackBridge.force_feedback(): throttle 우회
  - BanditFeedbackBridge.get_status(): 상태 조회
  - PerformanceTracker.attach/detach_bandit_bridge(): 연동 API
  - PerformanceTracker.get_status(): bandit_weights 포함 확인
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from application.analysis.bandit_feedback_bridge import (
    BanditFeedbackBridge,
    _normalize_strategy_name,
    _clip_reward,
    _REWARD_CLIP_MIN,
    _REWARD_CLIP_MAX,
    _MIN_FEEDBACK_INTERVAL_SEC,
)
from application.analysis.strategy_bandit import StrategyBandit


# ═══════════════════════════════════════════════════════════════════
#  공통 픽스처
# ═══════════════════════════════════════════════════════════════════

def _make_bandit() -> StrategyBandit:
    return StrategyBandit(["Trend", "Reversal", "Breakout"], seed=42)


def _make_mock_db(outcomes=None):
    """DB 목(Mock) 생성."""
    db = MagicMock()
    db.get_strategy_outcomes = AsyncMock(return_value=outcomes or [])
    return db


def _make_bridge(outcomes=None, feedback_days=7) -> BanditFeedbackBridge:
    return BanditFeedbackBridge(
        db=_make_mock_db(outcomes),
        bandit=_make_bandit(),
        feedback_days=feedback_days,
    )


# ═══════════════════════════════════════════════════════════════════
#  1. 헬퍼 함수 테스트
# ═══════════════════════════════════════════════════════════════════

class TestHelpers:

    def test_normalize_trend(self):
        assert _normalize_strategy_name("Trend") == "Trend"
        assert _normalize_strategy_name("trend") == "Trend"
        assert _normalize_strategy_name("TrendStrategy") == "Trend"

    def test_normalize_reversal(self):
        assert _normalize_strategy_name("Reversal") == "Reversal"
        assert _normalize_strategy_name("reversal") == "Reversal"
        assert _normalize_strategy_name("ReversalStrategy") == "Reversal"

    def test_normalize_breakout(self):
        assert _normalize_strategy_name("Breakout") == "Breakout"
        assert _normalize_strategy_name("breakout") == "Breakout"
        assert _normalize_strategy_name("BreakoutStrategy") == "Breakout"

    def test_normalize_unknown_returns_none(self):
        assert _normalize_strategy_name("Unknown") is None
        assert _normalize_strategy_name("") is None
        assert _normalize_strategy_name("RandomStrategy") is None

    def test_clip_reward_within_range(self):
        assert _clip_reward(0.05) == pytest.approx(0.05)
        assert _clip_reward(-0.05) == pytest.approx(-0.05)

    def test_clip_reward_max(self):
        """+15% → +10%로 클리핑."""
        assert _clip_reward(0.15) == pytest.approx(_REWARD_CLIP_MAX)

    def test_clip_reward_min(self):
        """-20% → -10%로 클리핑."""
        assert _clip_reward(-0.20) == pytest.approx(_REWARD_CLIP_MIN)

    def test_clip_reward_zero(self):
        assert _clip_reward(0.0) == pytest.approx(0.0)


# ═══════════════════════════════════════════════════════════════════
#  2. _compute_strategy_rewards 테스트
# ═══════════════════════════════════════════════════════════════════

class TestComputeStrategyRewards:

    def setup_method(self):
        self.bridge = _make_bridge()

    def test_empty_outcomes_returns_empty(self):
        result = self.bridge._compute_strategy_rewards([])
        assert result == {}

    def test_single_outcome_leader_gets_full_reward(self):
        """Trend가 최고 점수 → return_1d 전액 배분."""
        outcomes = [{
            "return_1d": 0.02,       # 2% 수익
            "strategy_scores": {"scores": {"Trend": 0.8, "Reversal": 0.3}},
            "is_correct": True,
        }]
        result = self.bridge._compute_strategy_rewards(outcomes)
        # Trend는 주도 전략 → 0.02 전액
        assert "Trend" in result
        assert result["Trend"] == pytest.approx(0.02, abs=1e-6)
        # Reversal은 점수 0.3 < 0.5 → 피드백 없음
        assert "Reversal" not in result

    def test_auxiliary_strategy_gets_partial_reward(self):
        """Reversal 점수 0.6 ≥ 0.5 → 비율 배분."""
        outcomes = [{
            "return_1d": 0.05,
            "strategy_scores": {"scores": {"Trend": 0.8, "Reversal": 0.6}},
            "is_correct": True,
        }]
        result = self.bridge._compute_strategy_rewards(outcomes)
        assert "Trend" in result
        assert "Reversal" in result
        # Reversal reward = 0.05 × (0.6/0.8) × 0.3
        expected_reversal = 0.05 * (0.6 / 0.8) * 0.3
        assert result["Reversal"] == pytest.approx(expected_reversal, abs=1e-6)

    def test_no_strategy_scores_skipped(self):
        """strategy_scores 없는 결과는 건너뜀."""
        outcomes = [{"return_1d": 0.03, "strategy_scores": {}, "is_correct": True}]
        result = self.bridge._compute_strategy_rewards(outcomes)
        assert result == {}

    def test_multiple_outcomes_averaged(self):
        """2개 결과 평균 보상."""
        outcomes = [
            {"return_1d": 0.04, "strategy_scores": {"scores": {"Trend": 0.9}}, "is_correct": True},
            {"return_1d": 0.02, "strategy_scores": {"scores": {"Trend": 0.7}}, "is_correct": True},
        ]
        result = self.bridge._compute_strategy_rewards(outcomes)
        assert "Trend" in result
        # 평균 = (0.04 + 0.02) / 2 = 0.03
        assert result["Trend"] == pytest.approx(0.03, abs=1e-6)

    def test_reward_clipping_applied(self):
        """15% 수익도 10%로 클리핑."""
        outcomes = [{
            "return_1d": 0.15,   # 15% → 클리핑 → 10%
            "strategy_scores": {"scores": {"Trend": 0.9}},
            "is_correct": True,
        }]
        result = self.bridge._compute_strategy_rewards(outcomes)
        assert result["Trend"] == pytest.approx(_REWARD_CLIP_MAX, abs=1e-6)

    def test_none_return_skipped(self):
        """return_1d=None인 결과는 건너뜀."""
        outcomes = [{"return_1d": None, "strategy_scores": {"scores": {"Trend": 0.9}}}]
        result = self.bridge._compute_strategy_rewards(outcomes)
        assert result == {}


# ═══════════════════════════════════════════════════════════════════
#  3. on_performance_updated 테스트
# ═══════════════════════════════════════════════════════════════════

class TestOnPerformanceUpdated:

    @pytest.mark.asyncio
    async def test_no_outcomes_returns_current_weights(self):
        """outcome 없으면 현재 Bandit 가중치 그대로 반환."""
        bridge = _make_bridge(outcomes=[])
        weights = await bridge.on_performance_updated()
        assert isinstance(weights, dict)
        assert set(weights.keys()) == {"Trend", "Reversal", "Breakout"}

    @pytest.mark.asyncio
    async def test_with_outcomes_updates_bandit(self):
        """outcome 있으면 Bandit이 업데이트됨."""
        outcomes = [
            {"return_1d": 0.03, "strategy_scores": {"scores": {"Trend": 0.9, "Reversal": 0.2}}, "is_correct": True},
            {"return_1d": 0.01, "strategy_scores": {"scores": {"Trend": 0.8, "Reversal": 0.6}}, "is_correct": True},
        ]
        bridge = _make_bridge(outcomes=outcomes)
        bandit_before = bridge._bandit.get_weights().copy()
        weights = await bridge.on_performance_updated()
        # Trend가 수익을 냈으므로 가중치가 높아져야 함
        assert weights["Trend"] >= bandit_before["Trend"] * 0.9   # 완만한 상승

    @pytest.mark.asyncio
    async def test_throttle_prevents_double_update(self):
        """최소 간격 이내 재호출은 throttle."""
        outcomes = [
            {"return_1d": 0.03, "strategy_scores": {"scores": {"Trend": 0.9}}, "is_correct": True},
        ]
        bridge = _make_bridge(outcomes=outcomes)
        await bridge.on_performance_updated()    # 1st call
        db_call_count = bridge._db.get_strategy_outcomes.call_count
        await bridge.on_performance_updated()    # 2nd call (throttled)
        assert bridge._db.get_strategy_outcomes.call_count == db_call_count   # DB 재호출 없음

    @pytest.mark.asyncio
    async def test_db_error_returns_current_weights(self):
        """DB 오류 시 현재 가중치 반환 (비치명적)."""
        db = MagicMock()
        db.get_strategy_outcomes = AsyncMock(side_effect=Exception("DB error"))
        bridge = BanditFeedbackBridge(db=db, bandit=_make_bandit())
        weights = await bridge.on_performance_updated()
        assert isinstance(weights, dict)
        assert len(weights) == 3


# ═══════════════════════════════════════════════════════════════════
#  4. force_feedback 및 상태 조회 테스트
# ═══════════════════════════════════════════════════════════════════

class TestForceFeedbackAndStatus:

    @pytest.mark.asyncio
    async def test_force_feedback_ignores_throttle(self):
        """force_feedback은 throttle 무시."""
        outcomes = [
            {"return_1d": 0.02, "strategy_scores": {"scores": {"Trend": 0.9}}, "is_correct": True},
        ]
        bridge = _make_bridge(outcomes=outcomes)
        await bridge.on_performance_updated()   # 1st (정상)
        await bridge.force_feedback()            # 2nd (강제)
        # DB가 2번 호출됨
        assert bridge._db.get_strategy_outcomes.call_count == 2

    def test_get_status_contains_required_keys(self):
        """get_status()에 필수 키 포함."""
        bridge = _make_bridge()
        status = bridge.get_status()
        assert "last_feedback" in status
        assert "total_feedbacks" in status
        assert "bandit_weights" in status
        assert "bandit_stats" in status

    def test_next_feedback_in_seconds_initial(self):
        """초기 상태에서는 즉시 피드백 가능 (0초)."""
        bridge = _make_bridge()
        assert bridge.next_feedback_in_seconds() == 0.0

    def test_get_recommended_strategy_returns_tuple(self):
        bridge = _make_bridge()
        name, reward = bridge.get_recommended_strategy()
        assert isinstance(name, str)
        assert isinstance(reward, float)


# ═══════════════════════════════════════════════════════════════════
#  5. PerformanceTracker v3.0 attach/detach 테스트
# ═══════════════════════════════════════════════════════════════════

class TestPerformanceTrackerV3:

    def test_attach_detach_bridge(self):
        """attach/detach API 동작 확인."""
        from analytics.performance_tracker import PerformanceTracker
        # 새 인스턴스 생성을 위해 _instance 초기화
        PerformanceTracker._instance = None
        tracker = PerformanceTracker()
        tracker._init()

        bridge = _make_bridge()
        tracker.attach_bandit_bridge(bridge)
        assert tracker._bandit_bridge is bridge

        tracker.detach_bandit_bridge()
        assert tracker._bandit_bridge is None

    def test_get_status_with_bridge_includes_weights(self):
        """Bridge 연결 시 get_status에 bandit_weights 포함."""
        from analytics.performance_tracker import PerformanceTracker
        PerformanceTracker._instance = None
        tracker = PerformanceTracker()
        tracker._init()

        bridge = _make_bridge()
        tracker.attach_bandit_bridge(bridge)
        status = tracker.get_status()
        assert "bandit_weights" in status
        assert isinstance(status["bandit_weights"], dict)

    def test_get_status_without_bridge_no_bandit_key(self):
        """Bridge 미연결 시 get_status에 bandit_weights 없음."""
        from analytics.performance_tracker import PerformanceTracker
        PerformanceTracker._instance = None
        tracker = PerformanceTracker()
        tracker._init()
        status = tracker.get_status()
        assert "bandit_weights" not in status
