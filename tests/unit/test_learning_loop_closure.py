"""학습 고리 연결 회귀 테스트 (2026-10-10).

🔴 발견된 끊긴 고리:
   ① Bandit이 학습해도 `get_weights()` 반환값이 **표시용으로만** 소비되어
      전략 선택에 전혀 반영되지 않았다 → `weight_sink` + `apply_learned_weights`.
   ② 대시보드가 `observability.ops_monitor.get_ops_snapshot`을 import 하는데
      그 함수가 없어 관측(품질/튜닝 제안) 섹션이 무음으로 비어 있었다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ── ① 학습 가중치 → 전략 선택 반영 ────────────────────────────────────
class TestApplyLearnedWeights:

    def _pipeline(self):
        from application.analysis.signal_pipeline import SignalPipeline
        p = SignalPipeline.__new__(SignalPipeline)
        p._base_strategy_weights = {"Trend": 0.33, "Reversal": 0.33, "Breakout": 0.33}
        p._strategy_weights = dict(p._base_strategy_weights)
        return p

    def test_uniform_weights_change_nothing(self):
        p = self._pipeline()
        out = p.apply_learned_weights({"Trend": 1 / 3, "Reversal": 1 / 3, "Breakout": 1 / 3})
        assert out == pytest.approx({"Trend": 0.33, "Reversal": 0.33, "Breakout": 0.33})

    def test_leader_gets_more_weight_clamped(self):
        p = self._pipeline()
        out = p.apply_learned_weights({"Trend": 0.9, "Reversal": 0.05, "Breakout": 0.05})
        assert out["Trend"] > 0.33
        assert out["Reversal"] < 0.33
        assert out["Trend"] <= 0.33 * 2.0 + 1e-9      # 상한 2.0x
        assert out["Reversal"] >= 0.33 * 0.5 - 1e-9    # 하한 0.5x

    def test_unknown_strategy_ignored(self):
        p = self._pipeline()
        out = p.apply_learned_weights({"UnknownStrategy": 0.9, "Trend": 1 / 3})
        assert out["Trend"] == pytest.approx(0.33)
        assert "UnknownStrategy" not in out

    def test_repeated_calls_do_not_drift(self):
        p = self._pipeline()
        w = {"Trend": 0.9, "Reversal": 0.05, "Breakout": 0.05}
        first = p.apply_learned_weights(w)
        for _ in range(10):
            p.apply_learned_weights(w)
        assert p.apply_learned_weights(w) == first, "기준값 대비 계산이라 누적 드리프트가 없어야 함"

    def test_empty_input_keeps_current(self):
        p = self._pipeline()
        before = dict(p._strategy_weights)
        assert p.apply_learned_weights({}) == before


# ── ② 대시보드 관측 스냅샷 ────────────────────────────────────────────
class TestOpsSnapshot:

    def test_snapshot_missing_before_publish(self):
        import observability.ops_monitor as om
        om._LATEST_SNAPSHOT = None
        assert om.get_ops_snapshot() is None

    def test_snapshot_available_after_call(self):
        import observability.ops_monitor as om
        mon = om.OpsMonitor()
        mon.snapshot()
        snap = om.get_ops_snapshot()
        assert isinstance(snap, dict)
        assert "quality" in snap and "tuning" in snap

    def test_dashboard_import_path_works(self):
        """대시보드가 실제로 쓰는 import 경로가 성공해야 한다."""
        from observability.ops_monitor import get_ops_snapshot   # noqa: F401


# ── ③ Bandit → 가중치 싱크 호출 ───────────────────────────────────────
class _FakeDB:
    async def get_strategy_outcomes(self, days: int = 7):
        return [{
            "return_1d": 0.02,
            "strategy_scores": {"scores": {"Trend": 0.8, "Reversal": 0.6}},
            "is_correct": True,
        }]


class _FakeBandit:
    def __init__(self):
        self.updated = None

    def get_weights(self):
        return {"Trend": 0.6, "Reversal": 0.2, "Breakout": 0.2}

    async def bulk_update(self, rewards):
        self.updated = rewards


class TestBanditWeightSink:

    @pytest.mark.asyncio
    async def test_sink_receives_learned_weights(self):
        from application.analysis.bandit_feedback_bridge import BanditFeedbackBridge
        got = []
        bridge = BanditFeedbackBridge(db=_FakeDB(), bandit=_FakeBandit(), weight_sink=got.append)
        weights = await bridge.on_performance_updated()
        assert got, "학습 후 weight_sink가 호출되어야 함(고리 연결)"
        assert got[0] == weights

    @pytest.mark.asyncio
    async def test_sink_failure_is_swallowed(self):
        from application.analysis.bandit_feedback_bridge import BanditFeedbackBridge

        def _boom(_w):
            raise RuntimeError("sink 실패")

        bridge = BanditFeedbackBridge(db=_FakeDB(), bandit=_FakeBandit(), weight_sink=_boom)
        weights = await bridge.on_performance_updated()
        assert weights, "싱크 실패해도 학습 결과 반환은 유지되어야 함"

    @pytest.mark.asyncio
    async def test_no_sink_still_works(self):
        from application.analysis.bandit_feedback_bridge import BanditFeedbackBridge
        bridge = BanditFeedbackBridge(db=_FakeDB(), bandit=_FakeBandit())
        assert await bridge.on_performance_updated()
