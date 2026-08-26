"""
tests/unit/test_root_cause_analyzer.py - v1.1 (Session 10 hotfix)
RootCauseAnalyzer 단위 테스트 (38개)
"""

import pytest
from dataclasses import FrozenInstanceError

from observability.root_cause_analyzer import (
    AnomalyInput,
    CauseCategory,
    CircuitBreakerInput,
    DriftInput,
    RecommendedAction,
    RootCauseAnalyzer,
    RootCauseReport,
    _RuleEngine,
)


def make_anomaly(is_anomaly=True, score=0.75, signal=0.5, latency=100.0):
    return AnomalyInput(is_anomaly=is_anomaly, anomaly_score=score,
                         signal_score=signal, latency_ms=latency, description="test")


def make_drift(name="trend", level="DRIFT", psi=0.25, drop=-0.12, retrain=True):
    return DriftInput(strategy_name=name, drift_level=level, psi_score=psi,
                       win_rate_drop=drop, should_retrain=retrain)


def make_cb(is_open=True, count=1, names=None):
    return CircuitBreakerInput(is_open=is_open, open_count=count,
                                breaker_names=names or ["ConsecutiveLossBreaker"],
                                status_summary="test")


class TestAnomalyInput:
    def test_creation(self):
        a = make_anomaly()
        assert a.is_anomaly is True
        assert a.anomaly_score == 0.75

    def test_frozen(self):
        a = make_anomaly()
        with pytest.raises(FrozenInstanceError):
            a.is_anomaly = False

    def test_not_anomaly(self):
        assert make_anomaly(is_anomaly=False).is_anomaly is False


class TestDriftInput:
    def test_creation(self):
        d = make_drift()
        assert d.strategy_name == "trend"
        assert d.drift_level == "DRIFT"

    def test_frozen(self):
        d = make_drift()
        with pytest.raises(FrozenInstanceError):
            d.drift_level = "STABLE"

    def test_stable_level(self):
        d = make_drift(level="STABLE", psi=0.05, drop=0.0, retrain=False)
        assert d.should_retrain is False


class TestCircuitBreakerInput:
    def test_creation(self):
        cb = make_cb()
        assert cb.is_open is True
        assert cb.open_count == 1

    def test_frozen(self):
        cb = make_cb()
        with pytest.raises(FrozenInstanceError):
            cb.is_open = False

    def test_multiple_breakers(self):
        assert len(make_cb(count=2, names=["BreakA", "BreakB"]).breaker_names) == 2


class TestRuleEngineEvaluate:
    def setup_method(self):
        self.engine = _RuleEngine()

    def test_no_inputs_returns_unknown(self):
        cause, conf, action, ev, sec, sev = self.engine.evaluate(None, None, None)
        assert cause == CauseCategory.UNKNOWN
        assert action == RecommendedAction.MONITOR

    def test_cb_open_returns_risk_breach(self):
        cause, *_ = self.engine.evaluate(None, None, make_cb(count=1))
        assert cause == CauseCategory.RISK_BREACH

    def test_cb_two_open_halt_trading(self):
        cause, conf, action, *_ = self.engine.evaluate(None, None, make_cb(count=2))
        assert cause == CauseCategory.RISK_BREACH
        assert action == RecommendedAction.HALT_TRADING

    def test_drift_critical_signal_degradation(self):
        cause, conf, action, *_ = self.engine.evaluate(None, make_drift(level="CRITICAL"), None)
        assert cause == CauseCategory.SIGNAL_DEGRADATION
        assert action == RecommendedAction.RETRAIN_MODEL

    def test_drift_warning_low_confidence(self):
        cause, conf, *_ = self.engine.evaluate(
            None, make_drift(level="WARNING", psi=0.12, drop=-0.03, retrain=False), None
        )
        assert cause == CauseCategory.SIGNAL_DEGRADATION
        assert conf < 0.65

    def test_anomaly_high_latency_connectivity(self):
        cause, *_ = self.engine.evaluate(make_anomaly(latency=3000.0), None, None)
        assert cause == CauseCategory.CONNECTIVITY

    def test_anomaly_out_of_range_data_quality(self):
        cause, *_ = self.engine.evaluate(make_anomaly(signal=1.5), None, None)
        assert cause == CauseCategory.DATA_QUALITY

    def test_cb_and_drift_returns_compound(self):
        cause, conf, action, ev, sec, sev = self.engine.evaluate(
            None, make_drift(level="DRIFT"), make_cb(count=1)
        )
        assert cause == CauseCategory.COMPOUND
        assert len(sec) >= 1


class TestRootCauseAnalyzerAnalyze:
    def setup_method(self):
        self.rca = RootCauseAnalyzer()

    def test_all_none_returns_unknown(self):
        report = self.rca.analyze()
        assert report.primary_cause == CauseCategory.UNKNOWN
        assert isinstance(report, RootCauseReport)

    def test_cb_open_risk_breach(self):
        assert self.rca.analyze(cb=make_cb(count=1)).primary_cause == CauseCategory.RISK_BREACH

    def test_drift_detected(self):
        # level="DRIFT" → 신뢰도 MEDIUM(0.65) < HIGH(0.85) 임계값
        # → 현재 구현의 단계적 대응 설계상 MONITOR가 정확한 결과
        report = self.rca.analyze(drift=make_drift(level="DRIFT"))
        assert report.primary_cause == CauseCategory.SIGNAL_DEGRADATION
        assert report.recommended_action == RecommendedAction.MONITOR

    def test_drift_critical_triggers_retrain(self):
        # level="CRITICAL" → 신뢰도 HIGH(0.85) → RETRAIN_MODEL
        report = self.rca.analyze(drift=make_drift(level="CRITICAL"))
        assert report.primary_cause == CauseCategory.SIGNAL_DEGRADATION
        assert report.recommended_action == RecommendedAction.RETRAIN_MODEL

    def test_anomaly_not_anomaly_no_cause(self):
        report = self.rca.analyze(anomaly=make_anomaly(is_anomaly=False))
        assert report.primary_cause == CauseCategory.UNKNOWN

    def test_evidence_list_not_empty(self):
        assert len(self.rca.analyze(cb=make_cb()).evidence) > 0

    def test_report_saved_to_history(self):
        self.rca.analyze(cb=make_cb())
        assert len(self.rca.recent_reports()) == 1

    def test_is_critical_on_data_quality(self):
        report = self.rca.analyze(anomaly=make_anomaly(signal=1.5, latency=10.0))
        assert report.is_critical

    def test_compound_cause_has_secondary(self):
        report = self.rca.analyze(drift=make_drift(level="DRIFT"), cb=make_cb())
        assert report.primary_cause == CauseCategory.COMPOUND
        assert len(report.secondary_causes) >= 1

    def test_requires_action_true_for_halt(self):
        assert self.rca.analyze(cb=make_cb(count=2)).requires_action is True

    def test_requires_action_false_for_monitor(self):
        assert self.rca.analyze().requires_action is False


class TestRootCauseReport:
    def _make(self):
        return RootCauseReport(
            primary_cause=CauseCategory.RISK_BREACH,
            confidence=0.90,
            recommended_action=RecommendedAction.HALT_TRADING,
            evidence=["차단기 발동: ConsecutiveLossBreaker (1개)"],
            secondary_causes=[],
            severity="CRITICAL",
            anomaly_input=None,
            drift_input=None,
            circuit_breaker_input=make_cb(),
        )

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for k in ["primary_cause", "confidence", "recommended_action", "evidence", "severity", "timestamp"]:
            assert k in d

    def test_is_critical_true(self):
        assert self._make().is_critical is True

    def test_frozen_immutability(self):
        r = self._make()
        with pytest.raises(FrozenInstanceError):
            r.severity = "INFO"

    def test_secondary_causes_in_dict(self):
        assert isinstance(self._make().to_dict()["secondary_causes"], list)


class TestRootCauseAnalyzerHistory:
    def test_recent_reports_order(self):
        rca = RootCauseAnalyzer()
        for _ in range(5):
            rca.analyze()
        assert len(rca.recent_reports(3)) == 3

    def test_critical_reports_filter(self):
        rca = RootCauseAnalyzer()
        rca.analyze()
        rca.analyze(cb=make_cb(count=2))
        assert len(rca.critical_reports()) >= 1

    def test_summary_counts(self):
        rca = RootCauseAnalyzer()
        rca.analyze()
        rca.analyze(cb=make_cb())
        s = rca.summary()
        assert s["total"] == 2
        assert "by_cause" in s

    def test_analyze_from_dict_basic(self):
        rca = RootCauseAnalyzer()
        report = rca.analyze_from_dict(cb_dict={"is_open": True, "open_count": 1, "breaker_names": ["CB1"]})
        assert report.primary_cause == CauseCategory.RISK_BREACH


class TestAnalyzeFromDict:
    def test_drift_dict(self):
        rca = RootCauseAnalyzer()
        report = rca.analyze_from_dict(drift_dict={
            "strategy_name": "trend", "drift_level": "DRIFT",
            "psi_score": 0.25, "win_rate_drop": -0.12, "should_retrain": True,
        })
        assert report.primary_cause == CauseCategory.SIGNAL_DEGRADATION

    def test_invalid_dict_safe_fallback(self):
        rca = RootCauseAnalyzer()
        report = rca.analyze_from_dict(anomaly_dict={"is_anomaly": "not_bool", "anomaly_score": "abc"})
        assert report is not None
