# tests/unit/test_hyperparameter_tuner_integration.py
# -*- coding: utf-8 -*-
"""Session 15 — HyperparameterTuner ↔ SignalPipeline 연동 테스트."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from application.analysis.hyperparameter_tuner import HyperparameterTuner, HistoricalSample
from application.analysis.signal_pipeline import SignalPipeline
from application.analysis.tuning_executor import TuningExecutor


@pytest.fixture
def pipeline():
    return SignalPipeline(db_manager=None)


@pytest.fixture
def small_dataset():
    return [
        HistoricalSample(
            action="BUY" if i % 3 else "HOLD",
            actual_return=0.02 if i % 2 == 0 else -0.01,
            sqi=0.6, confidence=0.55 + (i % 5) * 0.04, score=0.60 + (i % 6) * 0.03,
        )
        for i in range(30)
    ]


@pytest.fixture
def tuner():
    return HyperparameterTuner(n_trials=5, seed=42)


# ═══════════ SignalPipeline.update_hyperparameters() 검증 ═══════════

class TestUpdateHyperparametersValidation:
    def test_default_values(self, pipeline):
        h = pipeline.get_hyperparameters()
        assert h["buy_threshold"] == pytest.approx(0.62)
        assert h["sell_threshold"] == pytest.approx(0.38)
        assert h["min_confidence"] == pytest.approx(0.45)

    def test_buy_gt_sell_updates(self, pipeline):
        result = pipeline.update_hyperparameters({"buy_threshold": 0.70, "sell_threshold": 0.30})
        assert result["buy_threshold"] == pytest.approx(0.70)

    def test_buy_lte_sell_raises(self, pipeline):
        with pytest.raises(ValueError, match="buy_threshold"):
            pipeline.update_hyperparameters({"buy_threshold": 0.30, "sell_threshold": 0.50})

    def test_invalid_confidence_raises(self, pipeline):
        with pytest.raises(ValueError, match="min_confidence"):
            pipeline.update_hyperparameters({"min_confidence": 1.5})

    def test_partial_update_preserves_others(self, pipeline):
        pipeline.update_hyperparameters({"buy_threshold": 0.70})
        h = pipeline.get_hyperparameters()
        assert h["sell_threshold"] == pytest.approx(0.38)


class TestUpdateHyperparametersStrategyWeights:
    def test_trend_weight_applied_by_type(self, pipeline):
        pipeline.update_hyperparameters({"trend_weight": 0.55})
        h = pipeline.get_hyperparameters()
        assert h["trend_weight"] == pytest.approx(0.55)

    def test_all_three_strategy_weights(self, pipeline):
        pipeline.update_hyperparameters({
            "trend_weight": 0.50, "reversal_weight": 0.30, "breakout_weight": 0.20,
        })
        h = pipeline.get_hyperparameters()
        assert h["trend_weight"] == pytest.approx(0.50)
        assert h["reversal_weight"] == pytest.approx(0.30)
        assert h["breakout_weight"] == pytest.approx(0.20)

    def test_unspecified_weight_unchanged(self, pipeline):
        original = pipeline.get_hyperparameters()["breakout_weight"]
        pipeline.update_hyperparameters({"trend_weight": 0.55})
        assert pipeline.get_hyperparameters()["breakout_weight"] == pytest.approx(original)


class TestUpdateHyperparametersSqiV2:
    def test_normalization_when_sum_under_one(self, pipeline):
        pipeline.update_hyperparameters({
            "sqi_v2_momentum_w": 0.20, "sqi_v2_confidence_w": 0.30,
        })
        h = pipeline.get_hyperparameters()
        total = h["sqi_v2_momentum_w"] + h["sqi_v2_confidence_w"] + h["sqi_v2_consensus_w"]
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_normalization_when_sum_over_one(self, pipeline):
        pipeline.update_hyperparameters({
            "sqi_v2_momentum_w": 0.70, "sqi_v2_confidence_w": 0.60,
        })
        h = pipeline.get_hyperparameters()
        assert h["sqi_v2_consensus_w"] == pytest.approx(0.0)
        assert (h["sqi_v2_momentum_w"] + h["sqi_v2_confidence_w"]) == pytest.approx(1.0, abs=1e-6)


# ═══════════ HyperparameterTuner.apply_to_pipeline() ═══════════

class TestApplyToPipeline:
    def test_apply_without_optimize_raises(self, tuner, pipeline):
        with pytest.raises(RuntimeError, match="optimize"):
            tuner.apply_to_pipeline(pipeline)

    def test_apply_updates_full_param_space(self, tuner, small_dataset, pipeline):
        tuner.optimize(small_dataset)
        applied = tuner.apply_to_pipeline(pipeline)
        assert applied["buy_threshold"] > applied["sell_threshold"]
        assert "trend_weight" in applied

    def test_apply_with_explicit_result(self, tuner, small_dataset):
        result = tuner.optimize(small_dataset)
        p2 = SignalPipeline(db_manager=None)
        applied = tuner.apply_to_pipeline(p2, result=result)
        assert applied["buy_threshold"] == pytest.approx(result.best_params["buy_threshold"], abs=1e-6)


# ═══════════ TuningExecutor (DB/Telegram Mock) ═══════════

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_decisions_by_date_range = AsyncMock(return_value=[])
    db.get_outcome = AsyncMock(return_value=None)
    return db


@pytest.fixture
def mock_telegram():
    tg = MagicMock()
    tg.send_raw = AsyncMock()
    return tg


class TestTuningExecutorInsufficientSamples:
    @pytest.mark.asyncio
    async def test_empty_decisions_skips_and_alerts(self, mock_db, mock_telegram, pipeline):
        executor = TuningExecutor(mock_db, pipeline, telegram=mock_telegram, n_trials=3)
        result = await executor.run(days=30)
        assert result is None
        mock_telegram.send_raw.assert_called_once()
        assert "건너뜀" in mock_telegram.send_raw.call_args[0][0]


class TestTuningExecutorSuccessPath:
    @pytest.mark.asyncio
    async def test_sufficient_samples_applies_and_reports(self, mock_telegram, pipeline):
        db = MagicMock()
        decisions = [
            {"id": i, "action": "SIGNAL_ENTRY", "sqi": 0.6, "confidence": 0.6, "score": 0.65}
            for i in range(25)
        ]
        db.get_decisions_by_date_range = AsyncMock(return_value=decisions)
        db.get_outcome = AsyncMock(return_value={"return_1d": 0.02})

        executor = TuningExecutor(db, pipeline, telegram=mock_telegram, n_trials=3)
        applied = await executor.run(days=30)

        assert applied is not None
        assert applied["buy_threshold"] > applied["sell_threshold"]
        mock_telegram.send_raw.assert_called_once()
        assert "튜닝 완료" in mock_telegram.send_raw.call_args[0][0]


class TestTuningExecutorFailurePath:
    @pytest.mark.asyncio
    async def test_optimize_exception_sends_failure_alert(self, mock_telegram, pipeline):
        db = MagicMock()
        decisions = [
            {"id": i, "action": "SIGNAL_ENTRY", "sqi": 0.6, "confidence": 0.6, "score": 0.65}
            for i in range(25)
        ]
        db.get_decisions_by_date_range = AsyncMock(return_value=decisions)
        db.get_outcome = AsyncMock(return_value={"return_1d": 0.02})

        executor = TuningExecutor(db, pipeline, telegram=mock_telegram, n_trials=3)
        with patch.object(executor.tuner, "optimize", side_effect=RuntimeError("boom")):
            result = await executor.run(days=30)

        assert result is None
        assert "실패" in mock_telegram.send_raw.call_args[0][0]
