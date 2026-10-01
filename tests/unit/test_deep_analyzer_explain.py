# -*- coding: utf-8 -*-
"""tests/unit/test_deep_analyzer_explain.py - ML 설명 계층 배선 검증 (P3-1).

`observability/explainer_v2.py`는 구현돼 있었으나 프로덕션 호출이 0건이었다.
DeepAnalyzer의 ML 예측 경로에 배선했으며, 메인 워커 루프 부하를 막기 위해
(1) 중립 예측 제외 (2) 쿨다운 스로틀이 걸려 있는지 검증한다.
"""

from typing import Any, Dict

import pytest

from scanner.deep_analyzer import DeepAnalyzer

FEATURES = {"rsi": 72.0, "volume_ratio": 1.5, "momentum": 0.03, "macro_score": 0.6}


class _FakeLearner:
    _model_ready = True

    def __init__(self) -> None:
        self.calls = 0

    def predict_prob(self, features: Dict[str, Any]) -> float:
        self.calls += 1
        return 0.5 + 0.2 * (float(features.get("rsi", 50)) / 100.0)


def _analyzer() -> DeepAnalyzer:
    a = DeepAnalyzer()
    a.feedback_learner = _FakeLearner()
    return a


class TestExplainMl:
    def test_returns_none_for_neutral_prediction(self) -> None:
        a = _analyzer()
        assert a._explain_ml("005930", FEATURES, 0.50) is None
        assert a._explain_ml("005930", FEATURES, 0.55) is None  # 편차 < 0.1

    def test_produces_explanation_with_narrative(self) -> None:
        a = _analyzer()
        result = a._explain_ml("005930", FEATURES, 0.72)

        assert result is not None
        assert result["decision_id"] == "005930"
        assert isinstance(result["narrative"], str) and result["narrative"]
        assert "top_contributors" in result

    def test_throttled_within_cooldown(self) -> None:
        a = _analyzer()
        first = a._explain_ml("005930", FEATURES, 0.72)
        second = a._explain_ml("000660", FEATURES, 0.80)

        assert first is not None
        assert second is None  # 쿨다운 내 재호출 억제

    def test_allows_after_cooldown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        a = _analyzer()
        a._explain_cooldown_seconds = 0.0
        assert a._explain_ml("005930", FEATURES, 0.72) is not None
        assert a._explain_ml("000660", FEATURES, 0.80) is not None

    def test_handles_learner_failure(self) -> None:
        """모델이 죽어도 예외를 전파하지 않고 안전한 fallback 설명을 반환한다."""

        class _Broken:
            _model_ready = True

            def predict_prob(self, features: Dict[str, Any]) -> float:
                raise RuntimeError("모델 오류")

        a = DeepAnalyzer()
        a.feedback_learner = _Broken()
        result = a._explain_ml("005930", FEATURES, 0.72)

        assert result is None or isinstance(result, dict)
        if isinstance(result, dict):
            assert isinstance(result.get("narrative"), str)

    def test_ignores_non_numeric_features(self) -> None:
        a = _analyzer()
        result = a._explain_ml("005930", {"rsi": 72.0, "name": "삼성전자"}, 0.72)
        assert result is not None
        names = {c["feature_name"] for c in result["top_contributors"]}
        assert "name" not in names
