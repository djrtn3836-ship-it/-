# -*- coding: utf-8 -*-
"""
tests/unit/test_hyperparameter_tuner.py - Optuna 하이퍼파라미터 튜너 테스트 (v1.0)

테스트 클래스:
    TestHistoricalSample        (4개)  : DTO 생성 및 기본값
    TestComputeObjective        (8개)  : 목적 함수 계산
    TestSimulateActions         (7개)  : 행동 재시뮬레이션
    TestHyperparameterTunerInit (5개)  : 초기화 검증
    TestHyperparameterTunerOpt  (8개)  : optimize() 실행
    TestTuningResultToDict      (4개)  : TuningResult.to_dict()
    TestParamSpace              (4개)  : PARAM_SPACE 검증
    TestQuickTune               (4개)  : quick_tune() 편의 함수

총 44개 테스트
"""

import pytest

from application.analysis.hyperparameter_tuner import (
    DEFAULT_N_TRIALS,
    PARAM_SPACE,
    HistoricalSample,
    HyperparameterTuner,
    TuningResult,
    _simulate_actions,
    compute_objective,
    quick_tune,
)


# ─── 데이터셋 헬퍼 ───────────────────────────────────────────────

def _make_sample(
    action: str = "BUY",
    actual_return: float = 0.02,
    score: float = 0.70,
    confidence: float = 0.75,
    sqi: float = 0.70,
) -> HistoricalSample:
    return HistoricalSample(
        action=action,
        actual_return=actual_return,
        sqi=sqi,
        confidence=confidence,
        score=score,
    )


def _make_dataset(n: int = 30) -> list:
    """BUY/SELL/HOLD 혼합 데이터셋 생성."""
    samples = []
    for i in range(n):
        if i % 3 == 0:
            samples.append(_make_sample("BUY",  actual_return=0.03,  score=0.72))
        elif i % 3 == 1:
            samples.append(_make_sample("SELL", actual_return=-0.01, score=0.30))
        else:
            samples.append(_make_sample("HOLD", actual_return=0.0,   score=0.50))
    return samples


# ═══════════════════════════════════════════════════════════════════
#  HistoricalSample (4개)
# ═══════════════════════════════════════════════════════════════════

class TestHistoricalSample:
    def test_create_basic(self):
        s = _make_sample()
        assert s.action == "BUY"
        assert s.actual_return == pytest.approx(0.02)

    def test_default_values(self):
        s = HistoricalSample(action="HOLD", actual_return=0.0)
        assert s.sqi == 0.5
        assert s.confidence == 0.5
        assert s.score == 0.5

    def test_frozen_immutability(self):
        s = _make_sample()
        with pytest.raises((AttributeError, TypeError)):
            s.action = "SELL"  # type: ignore

    def test_negative_return(self):
        s = _make_sample(action="SELL", actual_return=-0.05)
        assert s.actual_return < 0


# ═══════════════════════════════════════════════════════════════════
#  compute_objective (8개)
# ═══════════════════════════════════════════════════════════════════

class TestComputeObjective:
    def _all_buy_positive(self, n: int = 10) -> list:
        return [{"action": "BUY", "actual_return": 0.05} for _ in range(n)]

    def _all_buy_negative(self, n: int = 10) -> list:
        return [{"action": "BUY", "actual_return": -0.05} for _ in range(n)]

    def test_empty_returns_minus_1(self):
        assert compute_objective([]) == -1.0

    def test_all_hold_returns_minus_1(self):
        simulated = [{"action": "HOLD", "actual_return": 0.0} for _ in range(5)]
        assert compute_objective(simulated) == -1.0

    def test_positive_returns_positive_objective(self):
        obj = compute_objective(self._all_buy_positive())
        assert obj > 0.0

    def test_negative_returns_lower_objective(self):
        pos = compute_objective(self._all_buy_positive())
        neg = compute_objective(self._all_buy_negative())
        assert pos > neg

    def test_high_win_rate_higher_objective(self):
        all_win = [{"action": "BUY", "actual_return": 0.02} for _ in range(10)]
        half_win = [
            {"action": "BUY", "actual_return": 0.02} for _ in range(5)
        ] + [
            {"action": "BUY", "actual_return": -0.02} for _ in range(5)
        ]
        assert compute_objective(all_win) > compute_objective(half_win)

    def test_single_trade_no_crash(self):
        obj = compute_objective([{"action": "BUY", "actual_return": 0.01}])
        assert isinstance(obj, float)

    def test_mixed_actions(self):
        mixed = [
            {"action": "BUY",  "actual_return": 0.03},
            {"action": "SELL", "actual_return": 0.01},
            {"action": "HOLD", "actual_return": 0.00},
        ]
        obj = compute_objective(mixed)
        assert isinstance(obj, float)

    def test_returns_float(self):
        obj = compute_objective(self._all_buy_positive())
        assert isinstance(obj, float)


# ═══════════════════════════════════════════════════════════════════
#  _simulate_actions (7개)
# ═══════════════════════════════════════════════════════════════════

class TestSimulateActions:
    def test_high_score_becomes_buy(self):
        samples = [_make_sample("HOLD", score=0.80, confidence=0.70)]
        result = _simulate_actions(samples, buy_threshold=0.65, sell_threshold=0.35, min_confidence=0.45)
        assert result[0]["action"] == "BUY"

    def test_low_score_becomes_sell(self):
        samples = [_make_sample("HOLD", score=0.20, confidence=0.70)]
        result = _simulate_actions(samples, buy_threshold=0.65, sell_threshold=0.35, min_confidence=0.45)
        assert result[0]["action"] == "SELL"

    def test_mid_score_becomes_hold(self):
        samples = [_make_sample("BUY", score=0.50, confidence=0.70)]
        result = _simulate_actions(samples, buy_threshold=0.65, sell_threshold=0.35, min_confidence=0.45)
        assert result[0]["action"] == "HOLD"

    def test_low_confidence_forces_hold(self):
        samples = [_make_sample("BUY", score=0.80, confidence=0.30)]
        result = _simulate_actions(samples, buy_threshold=0.65, sell_threshold=0.35, min_confidence=0.45)
        assert result[0]["action"] == "HOLD"

    def test_actual_return_preserved(self):
        samples = [_make_sample(actual_return=0.07)]
        result = _simulate_actions(samples, 0.62, 0.38, 0.45)
        assert result[0]["actual_return"] == pytest.approx(0.07)

    def test_empty_dataset_returns_empty(self):
        assert _simulate_actions([], 0.62, 0.38, 0.45) == []

    def test_all_samples_processed(self):
        samples = _make_dataset(10)
        result = _simulate_actions(samples, 0.62, 0.38, 0.45)
        assert len(result) == 10


# ═══════════════════════════════════════════════════════════════════
#  HyperparameterTuner 초기화 (5개)
# ═══════════════════════════════════════════════════════════════════

class TestHyperparameterTunerInit:
    def test_default_n_trials(self):
        tuner = HyperparameterTuner()
        assert tuner.n_trials == DEFAULT_N_TRIALS

    def test_custom_n_trials(self):
        tuner = HyperparameterTuner(n_trials=10)
        assert tuner.n_trials == 10

    def test_zero_n_trials_raises(self):
        with pytest.raises(ValueError, match="n_trials"):
            HyperparameterTuner(n_trials=0)

    def test_last_result_initially_none(self):
        tuner = HyperparameterTuner()
        assert tuner.last_result is None

    def test_param_space_static_method(self):
        space = HyperparameterTuner.param_space()
        assert isinstance(space, dict)
        assert "buy_threshold" in space


# ═══════════════════════════════════════════════════════════════════
#  HyperparameterTuner.optimize() (8개)
# ═══════════════════════════════════════════════════════════════════

class TestHyperparameterTunerOpt:
    """n_trials=5로 빠른 실행 보장."""

    @pytest.fixture
    def tuner(self):
        return HyperparameterTuner(n_trials=5, seed=42)

    @pytest.fixture
    def dataset(self):
        return _make_dataset(30)

    def test_optimize_returns_tuning_result(self, tuner, dataset):
        result = tuner.optimize(dataset)
        assert isinstance(result, TuningResult)

    def test_best_params_has_all_keys(self, tuner, dataset):
        result = tuner.optimize(dataset)
        for key in PARAM_SPACE:
            assert key in result.best_params

    def test_best_value_is_float(self, tuner, dataset):
        result = tuner.optimize(dataset)
        assert isinstance(result.best_value, float)

    def test_n_trials_count(self, tuner, dataset):
        result = tuner.optimize(dataset)
        assert result.n_trials == 5

    def test_elapsed_positive(self, tuner, dataset):
        result = tuner.optimize(dataset)
        assert result.elapsed_sec > 0.0

    def test_last_result_set_after_optimize(self, tuner, dataset):
        assert tuner.last_result is None
        tuner.optimize(dataset)
        assert tuner.last_result is not None

    def test_empty_dataset_raises(self, tuner):
        with pytest.raises(ValueError, match="dataset"):
            tuner.optimize([])

    def test_best_buy_threshold_in_range(self, tuner, dataset):
        result = tuner.optimize(dataset)
        lo = PARAM_SPACE["buy_threshold"]["low"]
        hi = PARAM_SPACE["buy_threshold"]["high"]
        assert lo <= result.best_params["buy_threshold"] <= hi


# ═══════════════════════════════════════════════════════════════════
#  TuningResult.to_dict() (4개)
# ═══════════════════════════════════════════════════════════════════

class TestTuningResultToDict:
    @pytest.fixture
    def result(self):
        tuner = HyperparameterTuner(n_trials=3, seed=0)
        return tuner.optimize(_make_dataset(20))

    def test_to_dict_has_required_keys(self, result):
        d = result.to_dict()
        for key in (
            "best_params", "best_value", "n_trials",
            "study_name", "elapsed_sec", "trial_history",
        ):
            assert key in d

    def test_best_value_rounded(self, result):
        d = result.to_dict()
        v = d["best_value"]
        assert v == round(v, 6)

    def test_best_params_keys(self, result):
        d = result.to_dict()
        for key in PARAM_SPACE:
            assert key in d["best_params"]

    def test_trial_history_is_list(self, result):
        d = result.to_dict()
        assert isinstance(d["trial_history"], list)


# ═══════════════════════════════════════════════════════════════════
#  PARAM_SPACE 검증 (4개)
# ═══════════════════════════════════════════════════════════════════

class TestParamSpace:
    def test_eight_params_defined(self):
        assert len(PARAM_SPACE) == 8

    def test_all_have_low_high(self):
        for name, bounds in PARAM_SPACE.items():
            assert "low" in bounds, f"{name} missing 'low'"
            assert "high" in bounds, f"{name} missing 'high'"

    def test_low_less_than_high(self):
        for name, bounds in PARAM_SPACE.items():
            assert bounds["low"] < bounds["high"], f"{name}: low >= high"

    def test_buy_threshold_above_sell(self):
        # buy_threshold 최저값 > sell_threshold 최고값 이어야 함
        assert PARAM_SPACE["buy_threshold"]["low"] > PARAM_SPACE["sell_threshold"]["high"]


# ═══════════════════════════════════════════════════════════════════
#  quick_tune() (4개)
# ═══════════════════════════════════════════════════════════════════

class TestQuickTune:
    def test_quick_tune_returns_result(self):
        result = quick_tune(_make_dataset(20), n_trials=3)
        assert isinstance(result, TuningResult)

    def test_quick_tune_respects_n_trials(self):
        result = quick_tune(_make_dataset(20), n_trials=4)
        assert result.n_trials == 4

    def test_quick_tune_best_params_exist(self):
        result = quick_tune(_make_dataset(20), n_trials=3)
        assert len(result.best_params) > 0

    def test_quick_tune_reproducible_with_seed(self):
        d = _make_dataset(20)
        r1 = quick_tune(d, n_trials=3, seed=7)
        r2 = quick_tune(d, n_trials=3, seed=7)
        # 동일 시드 → 동일 결과
        assert r1.best_value == pytest.approx(r2.best_value, abs=1e-6)
