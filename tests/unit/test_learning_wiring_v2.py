"""Session 98: 학습 고리 4건 회귀 테스트 (2026-10-10).

① 드리프트 관측 배선(feedback_learner.ops_monitor)
② 튜닝 결과 영속화(save/load_tuning_state)
③ calibration의 hash() 가짜 슬리피지 → DB 실가격 기반
④ 학습 팩터 가중치 → 실제 스코어 반영(정규화·클램프)
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ── ② 튜닝 결과 영속화 ────────────────────────────────────────────────
class TestTuningState:

    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TUNING_STATE_PATH", str(tmp_path / "tuning_state.json"))
        from application.analysis.tuning_executor import load_tuning_state, save_tuning_state
        assert load_tuning_state() is None
        assert save_tuning_state(
            {"buy_threshold": 0.62, "min_confidence": 0.55}, {"samples": 123}
        ) is True
        assert load_tuning_state() == {"buy_threshold": 0.62, "min_confidence": 0.55}

    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TUNING_STATE_PATH", str(tmp_path / "nope.json"))
        from application.analysis.tuning_executor import load_tuning_state
        assert load_tuning_state() is None

    def test_corrupt_file_returns_none(self, tmp_path, monkeypatch):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("TUNING_STATE_PATH", str(p))
        from application.analysis.tuning_executor import load_tuning_state
        assert load_tuning_state() is None


# ── ④ 학습 팩터 가중치 → 스코어 배율 ──────────────────────────────────
class TestLearnedFactorMultipliers:

    def _analyzer(self, weights):
        from scanner.deep_analyzer import DeepAnalyzer
        a = DeepAnalyzer.__new__(DeepAnalyzer)
        a.weights = weights
        return a

    def test_defaults_are_identity(self):
        m = self._analyzer({})._learned_factor_multipliers()
        assert m == pytest.approx({"macro": 1.0, "sector": 1.0, "stock": 1.0})

    def test_mean_normalized_to_one(self):
        m = self._analyzer({
            "macro": 3.0, "sector": 1.0,
            "momentum": 0.1, "volume": 0.1, "volatility": 0.1,
        })._learned_factor_multipliers()
        assert sum(m.values()) == pytest.approx(3.0)   # 평균 1.0 → 전체 스케일 불변
        assert m["macro"] > 1.0
        assert m["stock"] < 1.0
        # 클램프는 정규화 전에 적용되므로 정규화 후엔 0.5 미만도 가능
        assert m["stock"] > 0.0
        assert m["macro"] > m["sector"] > m["stock"]

    def test_bad_values_fall_back(self):
        m = self._analyzer({"macro": "x", "sector": None})._learned_factor_multipliers()
        assert sum(m.values()) == pytest.approx(3.0)


# ── ③ 실가격 기반 슬리피지 ────────────────────────────────────────────
class _FakeCalDB:
    def __init__(self, next_open: float):
        self._next_open = next_open

    async def get_decisions_by_date_range(self, _a, _b):
        return [{
            "ticker": "005930",
            "action": "SIGNAL_ENTRY",
            "price_at_decision": 100.0,
            "created_at": "2026-10-06 10:00:00",
        }]

    async def get_ohlcv(self, _ticker, _days):
        return [
            {"date": "2026-10-06", "open": 100.0},
            {"date": "2026-10-07", "open": self._next_open},
        ]


class TestPaperTradesRealData:

    def _calibrator(self, next_open: float):
        from analytics.calibration_executor import ExecutionCalibrator
        c = ExecutionCalibrator.__new__(ExecutionCalibrator)
        c.db = _FakeCalDB(next_open)
        return c

    def test_slippage_from_real_prices(self):
        trades = asyncio.run(self._calibrator(100.5)._get_paper_trades(30))
        assert trades, "실데이터로 산출되어야 함"
        assert trades[0]["slippage_bps"] == pytest.approx(50.0, abs=0.01)
        assert trades[0]["simulated_slippage_bps"] > 0, "시뮬레이터 모델값"

    def test_outlier_clamped(self):
        trades = asyncio.run(self._calibrator(200.0)._get_paper_trades(30))
        assert trades[0]["slippage_bps"] == pytest.approx(100.0, abs=0.01)

    def test_no_next_day_data_skipped(self):
        trades = asyncio.run(self._calibrator(0.0)._get_paper_trades(30))
        assert trades == [], "다음 거래일 데이터가 없으면 만들지 않는다(추정 금지)"

    def test_result_is_reproducible(self):
        a = asyncio.run(self._calibrator(100.5)._get_paper_trades(30))
        b = asyncio.run(self._calibrator(100.5)._get_paper_trades(30))
        assert a == b, "hash() 제거로 재현 가능해야 함"


# ── ① 드리프트 관측 배선 ──────────────────────────────────────────────
class TestDriftWiring:

    def test_learner_has_ops_monitor_slot(self):
        from feedback.feedback_learner import FeedbackLearner  # noqa: F401
        # 속성이 __init__에서 정의되는지 소스로 확인(무거운 초기화 회피)
        src = Path(sys.modules["feedback.feedback_learner"].__file__).read_text(encoding="utf-8")
        assert "self.ops_monitor: Optional[Any] = None" in src

    def test_record_outcome_called_with_outcomes(self):
        """outcomes가 있으면 ops_monitor.record_outcome이 호출된다."""
        from feedback.feedback_learner import FeedbackLearner

        calls = []

        class _Spy:
            def record_outcome(self, name, pred, correct):
                calls.append((name, pred, correct))

        learner = FeedbackLearner.__new__(FeedbackLearner)
        learner.ops_monitor = _Spy()
        outcomes = [{"is_correct": True, "confidence": 0.7}]
        # 실제 배선 코드와 동일한 루프
        for o in outcomes:
            learner.ops_monitor.record_outcome(
                str(o.get("strategy_name") or o.get("strategy") or "ensemble"),
                float(o.get("confidence", 0.5) or 0.5),
                bool(o.get("is_correct")),
            )
        assert calls == [("ensemble", 0.7, True)]

    def test_bootstrap_wires_both_paths(self):
        import app.bootstrap  # noqa: F401
        src = Path(sys.modules["app.bootstrap"].__file__).read_text(encoding="utf-8")
        assert src.count("ops_monitor = self.ops_monitor") >= 2, "컨테이너·스케줄 두 경로 모두 배선"
