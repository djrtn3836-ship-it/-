"""
observability/root_cause_analyzer.py - v1.0 (Session 10)

Root Cause Analysis (RCA): 장애/드리프트 발생 시 원인 자동 추론
- AnomalyDetector + ModelDriftDetector + CircuitBreakerManager 결과 교차 분석
- 규칙 기반 원인 분류 (우선순위 체인)
- 원인 카테고리: DATA_QUALITY / SIGNAL_DEGRADATION / RISK_BREACH / CONNECTIVITY / COMPOUND / UNKNOWN
- 권고 액션: HALT_TRADING / REDUCE_POSITION / RETRAIN_MODEL / MONITOR / IGNORE
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple

from core.logger import setup_logger

logger = setup_logger("root_cause_analyzer")

_CONFIDENCE_HIGH = 0.85
_CONFIDENCE_MEDIUM = 0.65
_CONFIDENCE_LOW = 0.45


class CauseCategory(str, Enum):
    DATA_QUALITY = "DATA_QUALITY"
    SIGNAL_DEGRADATION = "SIGNAL_DEGRADATION"
    RISK_BREACH = "RISK_BREACH"
    CONNECTIVITY = "CONNECTIVITY"
    COMPOUND = "COMPOUND"
    UNKNOWN = "UNKNOWN"


class RecommendedAction(str, Enum):
    HALT_TRADING = "HALT_TRADING"
    REDUCE_POSITION = "REDUCE_POSITION"
    RETRAIN_MODEL = "RETRAIN_MODEL"
    MONITOR = "MONITOR"
    IGNORE = "IGNORE"


@dataclass(frozen=True)
class AnomalyInput:
    is_anomaly: bool
    anomaly_score: float
    signal_score: float
    latency_ms: float
    description: str = ""


@dataclass(frozen=True)
class DriftInput:
    strategy_name: str
    drift_level: str
    psi_score: float
    win_rate_drop: float
    should_retrain: bool


@dataclass(frozen=True)
class CircuitBreakerInput:
    is_open: bool
    open_count: int
    breaker_names: List[str]
    status_summary: str = ""


@dataclass(frozen=True)
class RootCauseReport:
    primary_cause: CauseCategory
    confidence: float
    recommended_action: RecommendedAction
    evidence: List[str]
    secondary_causes: List[CauseCategory]
    severity: str
    anomaly_input: Optional[AnomalyInput]
    drift_input: Optional[DriftInput]
    circuit_breaker_input: Optional[CircuitBreakerInput]
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "primary_cause": self.primary_cause.value,
            "confidence": round(self.confidence, 3),
            "recommended_action": self.recommended_action.value,
            "evidence": self.evidence,
            "secondary_causes": [c.value for c in self.secondary_causes],
            "severity": self.severity,
            "timestamp": self.timestamp.isoformat(),
        }

    @property
    def is_critical(self) -> bool:
        return self.severity == "CRITICAL"

    @property
    def requires_action(self) -> bool:
        return self.recommended_action not in (
            RecommendedAction.MONITOR,
            RecommendedAction.IGNORE,
        )


class _RuleEngine:
    """우선순위 체인 규칙 엔진: RISK_BREACH > SIGNAL_DEGRADATION > DATA_QUALITY > CONNECTIVITY"""

    def evaluate(
        self,
        anomaly: Optional[AnomalyInput],
        drift: Optional[DriftInput],
        cb: Optional[CircuitBreakerInput],
    ) -> Tuple[CauseCategory, float, RecommendedAction, List[str], List[CauseCategory], str]:
        detected: List[Tuple[CauseCategory, float, str]] = []

        if cb and cb.is_open:
            conf = _CONFIDENCE_HIGH if cb.open_count >= 2 else _CONFIDENCE_MEDIUM
            ev = f"차단기 발동: {', '.join(cb.breaker_names)} ({cb.open_count}개)"
            detected.append((CauseCategory.RISK_BREACH, conf, ev))

        if drift:
            if drift.drift_level in ("CRITICAL", "DRIFT"):
                conf = _CONFIDENCE_HIGH if drift.drift_level == "CRITICAL" else _CONFIDENCE_MEDIUM
                ev = (
                    f"전략 [{drift.strategy_name}] 드리프트: "
                    f"PSI={drift.psi_score:.3f}, 승률하락={drift.win_rate_drop:+.1%}"
                )
                detected.append((CauseCategory.SIGNAL_DEGRADATION, conf, ev))
            elif drift.drift_level == "WARNING":
                ev = f"전략 [{drift.strategy_name}] 경고: PSI={drift.psi_score:.3f}"
                detected.append((CauseCategory.SIGNAL_DEGRADATION, _CONFIDENCE_LOW, ev))

        if anomaly and anomaly.is_anomaly:
            if anomaly.latency_ms > 2000:
                ev = f"이상 감지: score={anomaly.anomaly_score:.3f}, 지연={anomaly.latency_ms:.0f}ms"
                detected.append((CauseCategory.CONNECTIVITY, _CONFIDENCE_MEDIUM, ev))
            elif anomaly.signal_score < 0.0 or anomaly.signal_score > 1.0:
                ev = f"이상 신호값: score={anomaly.signal_score:.3f} (범위 초과)"
                detected.append((CauseCategory.DATA_QUALITY, _CONFIDENCE_HIGH, ev))
            else:
                ev = f"비정상 패턴: anomaly_score={anomaly.anomaly_score:.3f}"
                detected.append((CauseCategory.SIGNAL_DEGRADATION, _CONFIDENCE_MEDIUM, ev))

        if not detected:
            return (
                CauseCategory.UNKNOWN,
                _CONFIDENCE_LOW,
                RecommendedAction.MONITOR,
                ["명확한 원인 신호 없음"],
                [],
                "INFO",
            )

        if len(detected) >= 2:
            all_evidence = [d[2] for d in detected]
            max_conf = max(d[1] for d in detected)
            primary = detected[0][0]
            secondary = [d[0] for d in detected[1:]]
            action, severity = self._decide_action(primary, max_conf, [d[0] for d in detected])
            return (
                CauseCategory.COMPOUND,
                min(max_conf + 0.05, 1.0),
                action,
                all_evidence,
                secondary,
                severity,
            )

        cause, conf, ev = detected[0]
        action, severity = self._decide_action(cause, conf, [cause])
        return cause, conf, action, [ev], [], severity

    def _decide_action(
        self,
        primary_cause: CauseCategory,
        confidence: float,
        all_causes: List[CauseCategory],
    ) -> Tuple[RecommendedAction, str]:
        if CauseCategory.RISK_BREACH in all_causes:
            if confidence >= _CONFIDENCE_HIGH:
                return RecommendedAction.HALT_TRADING, "CRITICAL"
            return RecommendedAction.REDUCE_POSITION, "WARNING"

        if primary_cause == CauseCategory.SIGNAL_DEGRADATION:
            if confidence >= _CONFIDENCE_HIGH:
                return RecommendedAction.RETRAIN_MODEL, "WARNING"
            return RecommendedAction.MONITOR, "INFO"

        if primary_cause == CauseCategory.DATA_QUALITY:
            if confidence >= _CONFIDENCE_HIGH:
                return RecommendedAction.HALT_TRADING, "CRITICAL"
            return RecommendedAction.MONITOR, "WARNING"

        if primary_cause == CauseCategory.CONNECTIVITY:
            return RecommendedAction.MONITOR, "WARNING"

        if primary_cause == CauseCategory.COMPOUND:
            return RecommendedAction.REDUCE_POSITION, "WARNING"

        return RecommendedAction.IGNORE, "INFO"


class RootCauseAnalyzer:
    """장애/드리프트 발생 시 원인 자동 추론기."""

    def __init__(self, max_history: int = 200):
        self._rule_engine = _RuleEngine()
        self._reports: deque = deque(maxlen=max_history)

    def analyze(
        self,
        anomaly: Optional[AnomalyInput] = None,
        drift: Optional[DriftInput] = None,
        cb: Optional[CircuitBreakerInput] = None,
    ) -> RootCauseReport:
        try:
            cause, confidence, action, evidence, secondary, severity = (
                self._rule_engine.evaluate(anomaly, drift, cb)
            )
            report = RootCauseReport(
                primary_cause=cause,
                confidence=confidence,
                recommended_action=action,
                evidence=evidence,
                secondary_causes=secondary,
                severity=severity,
                anomaly_input=anomaly,
                drift_input=drift,
                circuit_breaker_input=cb,
            )
            self._reports.append(report)

            if report.is_critical:
                logger.warning(
                    f"[RCA CRITICAL] {cause.value} | action={action.value} | "
                    f"confidence={confidence:.2f} | evidence={evidence}"
                )
            elif report.severity == "WARNING":
                logger.info(
                    f"[RCA WARNING] {cause.value} | action={action.value} | "
                    f"confidence={confidence:.2f}"
                )
            return report

        except Exception as e:
            logger.error(f"RCA 분석 실패: {e}")
            return RootCauseReport(
                primary_cause=CauseCategory.UNKNOWN,
                confidence=0.0,
                recommended_action=RecommendedAction.MONITOR,
                evidence=[f"분석 오류: {str(e)}"],
                secondary_causes=[],
                severity="INFO",
                anomaly_input=anomaly,
                drift_input=drift,
                circuit_breaker_input=cb,
            )

    def analyze_from_dict(
        self,
        anomaly_dict: Optional[dict] = None,
        drift_dict: Optional[dict] = None,
        cb_dict: Optional[dict] = None,
    ) -> RootCauseReport:
        anomaly_in: Optional[AnomalyInput] = None
        drift_in: Optional[DriftInput] = None
        cb_in: Optional[CircuitBreakerInput] = None

        try:
            if anomaly_dict:
                anomaly_in = AnomalyInput(
                    is_anomaly=bool(anomaly_dict.get("is_anomaly", False)),
                    anomaly_score=float(anomaly_dict.get("anomaly_score", 0.5)),
                    signal_score=float(anomaly_dict.get("signal_score", 0.5)),
                    latency_ms=float(anomaly_dict.get("latency_ms", 0.0)),
                    description=str(anomaly_dict.get("description", "")),
                )
        except Exception as e:
            logger.warning(f"anomaly_dict 파싱 실패: {e}")

        try:
            if drift_dict:
                drift_in = DriftInput(
                    strategy_name=str(drift_dict.get("strategy_name", "unknown")),
                    drift_level=str(drift_dict.get("drift_level", "STABLE")),
                    psi_score=float(drift_dict.get("psi_score", 0.0)),
                    win_rate_drop=float(drift_dict.get("win_rate_drop", 0.0)),
                    should_retrain=bool(drift_dict.get("should_retrain", False)),
                )
        except Exception as e:
            logger.warning(f"drift_dict 파싱 실패: {e}")

        try:
            if cb_dict:
                cb_in = CircuitBreakerInput(
                    is_open=bool(cb_dict.get("is_open", False)),
                    open_count=int(cb_dict.get("open_count", 0)),
                    breaker_names=list(cb_dict.get("breaker_names", [])),
                    status_summary=str(cb_dict.get("status_summary", "")),
                )
        except Exception as e:
            logger.warning(f"cb_dict 파싱 실패: {e}")

        return self.analyze(anomaly=anomaly_in, drift=drift_in, cb=cb_in)

    def recent_reports(self, n: int = 10) -> List[RootCauseReport]:
        reports = list(self._reports)
        return list(reversed(reports))[:n]

    def critical_reports(self, n: int = 10) -> List[RootCauseReport]:
        return [r for r in self.recent_reports(100) if r.is_critical][:n]

    def action_required_count(self) -> int:
        return sum(1 for r in self._reports if r.requires_action)

    def summary(self) -> dict:
        all_reports = list(self._reports)
        if not all_reports:
            return {"total": 0, "critical": 0, "by_cause": {}, "by_action": {}}

        by_cause: dict = {}
        by_action: dict = {}
        critical_count = 0

        for r in all_reports:
            by_cause[r.primary_cause.value] = by_cause.get(r.primary_cause.value, 0) + 1
            by_action[r.recommended_action.value] = by_action.get(r.recommended_action.value, 0) + 1
            if r.is_critical:
                critical_count += 1

        return {
            "total": len(all_reports),
            "critical": critical_count,
            "action_required": self.action_required_count(),
            "by_cause": by_cause,
            "by_action": by_action,
        }
