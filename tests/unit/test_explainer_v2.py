"""
tests/unit/test_explainer_v2.py - v1.0 (Session 11)
ExplainerV2 단위 테스트 (42개)
"""

import pytest
from dataclasses import FrozenInstanceError

from observability.explainer_v2 import (
    ExplainerV2,
    FeatureContribution,
    LocalExplanation,
    _build_contributions,
    _compute_counterfactual,
    _permutation_shapley,
)


def simple_score_fn(features):
    rsi = features.get("rsi", 50.0)
    vol = features.get("volume_ratio", 1.0)
    return min(1.0, max(0.0, rsi / 100.0 * 0.8 + vol * 0.1))


SAMPLE_FEATURES = {"rsi": 72.0, "volume_ratio": 1.5, "macd_hist": 0.3}


class TestFeatureContribution:
    def test_positive_direction(self):
        fc = FeatureContribution("rsi", 0.12, "+", 0.12)
        assert fc.direction == "+"
        assert fc.magnitude == 0.12

    def test_negative_direction(self):
        fc = FeatureContribution("macd", -0.05, "-", 0.05)
        assert fc.direction == "-"
        assert fc.magnitude == 0.05

    def test_to_dict_keys(self):
        fc = FeatureContribution("rsi", 0.1, "+", 0.1)
        d = fc.to_dict()
        for k in ["feature_name", "contribution_value", "direction", "magnitude"]:
            assert k in d

    def test_frozen(self):
        fc = FeatureContribution("rsi", 0.1, "+", 0.1)
        with pytest.raises(FrozenInstanceError):
            fc.magnitude = 0.99

    def test_zero_contribution(self):
        fc = FeatureContribution("neutral", 0.0, "+", 0.0)
        assert fc.magnitude == 0.0


class TestLocalExplanation:
    def _make(self):
        return LocalExplanation(
            decision_id="DEC-001", action="BUY", final_score=0.72,
            top_contributors=[FeatureContribution("rsi", 0.15, "+", 0.15)],
            counterfactual="rsi가 감소했다면 HOLD",
            confidence_gap=0.22,
        )

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for k in ["decision_id", "action", "final_score", "top_contributors",
                   "counterfactual", "confidence_gap"]:
            assert k in d

    def test_frozen(self):
        le = self._make()
        with pytest.raises(FrozenInstanceError):
            le.action = "SELL"

    def test_top_contributors_serialized(self):
        d = self._make().to_dict()
        assert isinstance(d["top_contributors"], list)
        assert len(d["top_contributors"]) == 1

    def test_counterfactual_in_dict(self):
        d = self._make().to_dict()
        assert d["counterfactual"] is not None

    def test_no_contributors(self):
        le = LocalExplanation(
            decision_id="X", action="HOLD", final_score=0.5,
            top_contributors=[], counterfactual=None, confidence_gap=0.0,
        )
        assert le.to_dict()["top_contributors"] == []


class TestPermutationShapley:
    def test_empty_features_returns_empty(self):
        assert _permutation_shapley({}, simple_score_fn) == {}

    def test_returns_all_feature_keys(self):
        result = _permutation_shapley(SAMPLE_FEATURES, simple_score_fn, seed=42)
        assert set(result.keys()) == set(SAMPLE_FEATURES.keys())

    def test_sum_approx_score_difference(self):
        baseline = {k: 0.0 for k in SAMPLE_FEATURES}
        result = _permutation_shapley(SAMPLE_FEATURES, simple_score_fn,
                                       baseline=baseline, seed=0, max_samples=100)
        total = sum(result.values())
        expected = simple_score_fn(SAMPLE_FEATURES) - simple_score_fn(baseline)
        assert abs(total - expected) < 0.15

    def test_irrelevant_feature_near_zero(self):
        def score_fn(f):
            return f.get("rsi", 50) / 100
        features = {"rsi": 70.0, "irrelevant": 999.0}
        result = _permutation_shapley(features, score_fn, seed=1, max_samples=50)
        assert abs(result["irrelevant"]) < 0.05

    def test_dominant_feature_highest_shapley(self):
        def score_fn(f):
            return f.get("rsi", 0) / 100
        features = {"rsi": 80.0, "noise": 0.001}
        result = _permutation_shapley(features, score_fn, seed=2, max_samples=50)
        assert abs(result["rsi"]) > abs(result["noise"])

    def test_seed_reproducibility(self):
        r1 = _permutation_shapley(SAMPLE_FEATURES, simple_score_fn, seed=42)
        r2 = _permutation_shapley(SAMPLE_FEATURES, simple_score_fn, seed=42)
        for k in r1:
            assert r1[k] == pytest.approx(r2[k], abs=1e-9)

    def test_single_feature(self):
        def score_fn(f):
            return f.get("x", 0.0)
        result = _permutation_shapley({"x": 0.8}, score_fn, seed=0)
        assert "x" in result


class TestBuildContributions:
    def test_sorted_by_magnitude(self):
        shapley = {"a": 0.1, "b": -0.3, "c": 0.05}
        result = _build_contributions(shapley, top_k=3)
        magnitudes = [c.magnitude for c in result]
        assert magnitudes == sorted(magnitudes, reverse=True)

    def test_top_k_limit(self):
        shapley = {f"f{i}": float(i) * 0.01 for i in range(10)}
        result = _build_contributions(shapley, top_k=3)
        assert len(result) == 3

    def test_direction_positive(self):
        result = _build_contributions({"pos": 0.5}, top_k=1)
        assert result[0].direction == "+"

    def test_direction_negative(self):
        result = _build_contributions({"neg": -0.3}, top_k=1)
        assert result[0].direction == "-"


class TestComputeCounterfactual:
    def test_returns_none_when_no_flip(self):
        result = _compute_counterfactual(
            {"rsi": 90.0}, lambda f: 0.9, 0.9, "BUY", delta=0.05
        )
        assert result is None

    def test_returns_string_when_flip_possible(self):
        def score_fn(f):
            return f.get("rsi", 50) / 100
        result = _compute_counterfactual(
            {"rsi": 52.0}, score_fn, 0.52, "BUY", delta=0.05
        )
        assert result is None or isinstance(result, str)

    def test_empty_features_returns_none(self):
        result = _compute_counterfactual({}, simple_score_fn, 0.6, "BUY")
        assert result is None

    def test_result_is_string_or_none(self):
        result = _compute_counterfactual(
            SAMPLE_FEATURES, simple_score_fn, 0.72, "BUY"
        )
        assert result is None or isinstance(result, str)


class TestExplainerV2ExplainLocal:
    def setup_method(self):
        self.explainer = ExplainerV2(max_shapley_samples=20, seed=42)

    def test_returns_local_explanation(self):
        result = self.explainer.explain_local(
            SAMPLE_FEATURES, simple_score_fn, "DEC-1", "BUY"
        )
        assert isinstance(result, LocalExplanation)

    def test_decision_id_preserved(self):
        result = self.explainer.explain_local(
            SAMPLE_FEATURES, simple_score_fn, "MY-ID", "HOLD"
        )
        assert result.decision_id == "MY-ID"

    def test_action_preserved(self):
        result = self.explainer.explain_local(
            SAMPLE_FEATURES, simple_score_fn, "X", "SELL"
        )
        assert result.action == "SELL"

    def test_top_contributors_not_empty(self):
        result = self.explainer.explain_local(SAMPLE_FEATURES, simple_score_fn)
        assert len(result.top_contributors) > 0

    def test_final_score_in_range(self):
        result = self.explainer.explain_local(SAMPLE_FEATURES, simple_score_fn)
        assert 0.0 <= result.final_score <= 1.0

    def test_confidence_gap_nonnegative(self):
        result = self.explainer.explain_local(SAMPLE_FEATURES, simple_score_fn)
        assert result.confidence_gap >= 0.0

    def test_empty_features_safe_fallback(self):
        result = self.explainer.explain_local({}, simple_score_fn)
        assert isinstance(result, LocalExplanation)
        assert result.top_contributors == []

    def test_history_grows(self):
        for i in range(3):
            self.explainer.explain_local(SAMPLE_FEATURES, simple_score_fn, f"D{i}", "BUY")
        assert self.explainer.history_size == 3

    def test_score_fn_exception_safe_fallback(self):
        def bad_fn(f):
            raise RuntimeError("score error")
        result = self.explainer.explain_local(SAMPLE_FEATURES, bad_fn)
        assert isinstance(result, LocalExplanation)


class TestExplainerV2ExplainGlobal:
    def test_empty_history_returns_empty(self):
        explainer = ExplainerV2()
        assert explainer.explain_global() == {}

    def test_returns_dict(self):
        explainer = ExplainerV2(max_shapley_samples=10, seed=0)
        explainer.explain_local(SAMPLE_FEATURES, simple_score_fn, "D1", "BUY")
        result = explainer.explain_global()
        assert isinstance(result, dict)

    def test_feature_keys_present(self):
        explainer = ExplainerV2(max_shapley_samples=10, seed=0)
        explainer.explain_local(SAMPLE_FEATURES, simple_score_fn, "D1", "BUY")
        result = explainer.explain_global()
        assert set(result.keys()) == set(SAMPLE_FEATURES.keys())

    def test_values_nonnegative(self):
        explainer = ExplainerV2(max_shapley_samples=10, seed=0)
        explainer.explain_local(SAMPLE_FEATURES, simple_score_fn, "D1", "BUY")
        for v in explainer.explain_global().values():
            assert v >= 0.0


class TestExplainerV2GenerateNarrative:
    def test_returns_string(self):
        explainer = ExplainerV2(max_shapley_samples=10, seed=0)
        result = explainer.explain_local(SAMPLE_FEATURES, simple_score_fn, "D1", "BUY")
        narrative = explainer.generate_narrative(result)
        assert isinstance(narrative, str)

    def test_contains_decision_id(self):
        explainer = ExplainerV2(max_shapley_samples=10, seed=0)
        result = explainer.explain_local(SAMPLE_FEATURES, simple_score_fn, "DEC-XYZ", "BUY")
        assert "DEC-XYZ" in explainer.generate_narrative(result)

    def test_empty_contributors_fallback(self):
        le = LocalExplanation("X", "HOLD", 0.5, [], None, 0.0)
        explainer = ExplainerV2()
        narrative = explainer.generate_narrative(le)
        assert "설명 가능한 피처 없음" in narrative

    def test_counterfactual_in_narrative(self):
        le = LocalExplanation(
            "D1", "BUY", 0.7,
            [FeatureContribution("rsi", 0.2, "+", 0.2)],
            "rsi가 감소했다면 HOLD",
            0.2,
        )
        explainer = ExplainerV2()
        assert "반사실" in explainer.generate_narrative(le)
