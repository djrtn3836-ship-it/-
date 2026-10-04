# -*- coding: utf-8 -*-
"""tests/unit/test_ops_monitor.py - 관측성 통합 파사드 배선 검증 (P3-1).

검증 항목:
    1. 학습 데이터 부족 시 무해하게 None 반환
    2. 학습 완료 후 극단값 → 이상 감지 → RCA 리포트 반환
    3. 조치 필요 리포트 → 알림 콜백 1회 호출
    4. 쿨다운 내 중복 알림 억제
    5. 비활성(enabled=False) → 완전 no-op
    6. 어떤 입력에도 예외를 전파하지 않음(매매 경로 보호)
    7. 드리프트 경로: 기준 수집 후 성능 저하 → 리포트 반환
"""

import random

import pytest

from observability.ops_monitor import OpsMonitor
from observability.root_cause_analyzer import (
    CauseCategory,
    RecommendedAction,
    RootCauseReport,
)


def _normal(**overrides: float) -> dict:
    """현실적인 정상 신호(약간의 변동 포함) 1건 생성."""
    base = dict(
        score=0.5 + random.gauss(0, 0.02),
        confidence=0.5 + random.gauss(0, 0.02),
        latency_ms=100.0 + random.gauss(0, 8.0),
        sqi=0.5 + random.gauss(0, 0.02),
    )
    base.update(overrides)
    return base


EXTREME = dict(score=0.01, confidence=0.99, latency_ms=90000.0, sqi=0.02)


def _make_report(severity: str = "CRITICAL", action: RecommendedAction = RecommendedAction.HALT_TRADING) -> RootCauseReport:
    return RootCauseReport(
        primary_cause=CauseCategory.SIGNAL_DEGRADATION,
        confidence=0.9,
        recommended_action=action,
        evidence=["테스트 근거"],
        secondary_causes=[],
        severity=severity,
        anomaly_input=None,
        drift_input=None,
        circuit_breaker_input=None,
    )


class TestRecordSignal:
    def test_returns_none_while_learning(self) -> None:
        m = OpsMonitor()
        assert m.record_signal(**_normal(), ticker="005930") is None
        assert m.snapshot()["stats"]["signals_observed"] == 1

    def test_detects_anomaly_after_training(self) -> None:
        m = OpsMonitor()
        for _ in range(200):
            m.record_signal(**_normal())

        reports = [m.record_signal(**EXTREME) for _ in range(10)]
        assert any(r is not None for r in reports), "극단값 10회 중 이상 감지 실패"
        assert m.snapshot()["stats"]["anomalies"] >= 1

    def test_never_raises_on_bad_input(self) -> None:
        m = OpsMonitor()
        assert m.record_signal(score=None, confidence="x", latency_ms=object(), sqi=None) is None
        assert m.snapshot()["stats"]["errors"] >= 1


class TestAlerting:
    def test_alert_dispatched_once_for_actionable_report(self) -> None:
        sent: list = []
        m = OpsMonitor(on_alert=sent.append)
        m._handle_report(_make_report())

        assert len(sent) == 1
        assert "SIGNAL_DEGRADATION" in sent[0]
        assert m.snapshot()["stats"]["alerts_sent"] == 1

    def test_duplicate_alert_suppressed_by_cooldown(self) -> None:
        sent: list = []
        m = OpsMonitor(on_alert=sent.append, alert_cooldown_sec=1800.0)
        m._handle_report(_make_report())
        m._handle_report(_make_report())

        assert len(sent) == 1
        assert m.snapshot()["stats"]["alerts_suppressed"] == 1

    def test_monitor_only_report_does_not_alert(self) -> None:
        sent: list = []
        m = OpsMonitor(on_alert=sent.append)
        m._handle_report(_make_report(severity="INFO", action=RecommendedAction.MONITOR))

        assert sent == []
        assert m.snapshot()["stats"]["reports"] == 1

    def test_no_callback_does_not_raise(self) -> None:
        m = OpsMonitor(on_alert=None)
        m._handle_report(_make_report())
        assert m.snapshot()["stats"]["alerts_sent"] == 1


class TestDisabled:
    def test_disabled_is_noop(self) -> None:
        sent: list = []
        m = OpsMonitor(enabled=False, on_alert=sent.append)

        assert m.record_signal(**EXTREME) is None
        assert m.record_outcome("Trend", 0.9, True) is None
        assert m.snapshot()["stats"]["signals_observed"] == 0
        assert sent == []


class TestDriftPath:
    def test_drift_detected_after_baseline(self) -> None:
        m = OpsMonitor()
        for _ in range(60):
            m.record_outcome("Trend", 0.9, True)

        reports = [m.record_outcome("Trend", 0.05, False) for _ in range(60)]
        assert any(r is not None for r in reports), "드리프트 미감지"
        assert m.snapshot()["stats"]["drift_reports"] >= 1

    def test_outcome_never_raises(self) -> None:
        m = OpsMonitor()
        assert m.record_outcome(None, "bad", None) is None


class TestSnapshot:
    def test_snapshot_shape(self) -> None:
        m = OpsMonitor()
        m.record_signal(**_normal())
        snap = m.snapshot()

        assert set(snap) >= {"enabled", "stats", "last_report", "recent_count", "quality", "tuning"}
        assert snap["enabled"] is True
        assert snap["recent_count"] == 0


class TestQualitySnapshot:
    """P7: 알림 품질 지표 및 임계 튜닝."""

    def test_initial_shape(self) -> None:
        q = OpsMonitor().quality_snapshot()

        assert set(q) >= {
            "signals_observed", "anomalies", "anomaly_rate", "reports",
            "alerts_sent", "alert_rate", "suppression_ratio", "thresholds", "hints",
        }
        assert q["thresholds"]["anomaly_threshold"] == 0.65
        assert q["hints"] == []

    def test_hint_when_suppression_high(self) -> None:
        m = OpsMonitor(on_alert=lambda msg: None, alert_cooldown_sec=1800.0)
        m._handle_report(_make_report())
        m._handle_report(_make_report())  # 쿨다운 억제
        m._handle_report(_make_report())

        q = m.quality_snapshot()
        assert q["suppression_ratio"] > 0.5
        assert any("억제" in h for h in q["hints"])

    def test_env_override_thresholds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPS_ANOMALY_THRESHOLD", "0.8")
        monkeypatch.setenv("OPS_ANOMALY_WINDOW", "50")
        monkeypatch.setenv("OPS_DRIFT_WINDOW", "80")

        t = OpsMonitor().quality_snapshot()["thresholds"]
        assert t["anomaly_threshold"] == 0.8
        assert t["anomaly_window"] == 50
        assert t["drift_window"] == 80

    def test_invalid_env_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPS_ANOMALY_THRESHOLD", "abc")
        assert OpsMonitor().quality_snapshot()["thresholds"]["anomaly_threshold"] == 0.65


class TestTuningSuggestions:
    """P10-2: 알림 품질 기반 임계 조정 제안(자동 적용 안 함)."""

    def test_no_suggestions_when_quiet(self) -> None:
        r = OpsMonitor().suggest_tuning()

        assert r["applied"] is False
        assert r["suggestions"] == []

    def test_suggests_cooldown_shortening_on_high_suppression(self) -> None:
        m = OpsMonitor()
        m._stats.update({"alerts_sent": 1, "alerts_suppressed": 9, "reports": 10,
                         "signals_observed": 100})

        r = m.suggest_tuning()
        targets = {s["target"] for s in r["suggestions"]}

        assert "OPS_ALERT_COOLDOWN_SEC" in targets
        cooldown = next(s for s in r["suggestions"] if s["target"] == "OPS_ALERT_COOLDOWN_SEC")
        assert cooldown["suggested"] < cooldown["current"]

    def test_suggests_threshold_up_on_many_anomalies(self) -> None:
        m = OpsMonitor()
        m._stats.update({"signals_observed": 100, "anomalies": 30, "reports": 30})

        s = next(x for x in m.suggest_tuning()["suggestions"] if x["target"] == "OPS_ANOMALY_THRESHOLD")
        assert s["suggested"] > s["current"]

    def test_suggests_threshold_down_when_never_detecting(self) -> None:
        m = OpsMonitor()
        m._stats.update({"signals_observed": 1000, "anomalies": 0})

        s = next(x for x in m.suggest_tuning()["suggestions"] if x["target"] == "OPS_ANOMALY_THRESHOLD")
        assert s["suggested"] < s["current"]

    def test_never_auto_applies(self) -> None:
        """제안은 항상 미적용 상태여야 한다(운영자 승인 없이 임계 변경 금지)."""
        m = OpsMonitor()
        m._stats.update({"signals_observed": 1000, "anomalies": 200, "alerts_sent": 1,
                         "alerts_suppressed": 20, "reports": 21})

        assert m.suggest_tuning()["applied"] is False

    def test_snapshot_includes_quality_and_tuning(self) -> None:
        snap = OpsMonitor().snapshot()

        assert "quality" in snap and "tuning" in snap
