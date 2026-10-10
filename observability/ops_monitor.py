# -*- coding: utf-8 -*-
"""observability/ops_monitor.py - 관측성 4종 통합 파사드 (P3-1 배선).

배경
----
`observability/` 안에 개별 완성돼 있으나 프로덕션에서 한 번도 호출되지 않던
(테스트만 존재하는) 컴포넌트 4종을 하나의 체인으로 묶어 **실제로 동작**시킨다.

    AnomalyDetector      ─┐
    ModelDriftDetector   ─┼─→ RootCauseAnalyzer ─→ RootCauseReport ─→ (스로틀) Telegram
    (선택) CircuitBreaker ─┘

배선 위치
---------
- `SignalPipeline.process()` → :meth:`record_signal` (신호 1건당 1회, 예외 전파 금지)
- 체결/성과 종료 경로       → :meth:`record_outcome` (Phase 2에서 활성화)
- `app/bootstrap.py`        → 인스턴스 생성 + 텔레그램 알림 콜백 주입

설계 원칙
---------
- **관측은 절대 매매를 방해하지 않는다**: 모든 public 메서드는 예외를 삼키고 로그만 남긴다.
- **알림 폭주 방지**: 조치 필요(`requires_action`)·치명(`is_critical`) 리포트만,
  쿨다운(기본 30분) 내 중복 억제.
- **결합 최소화**: 텔레그램은 콜백으로 주입(동기/비동기 모두 지원).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional

from observability.anomaly_detector import AnomalyDetector, AnomalyReport
from observability.model_drift_detector import DriftReport, ModelDriftDetector
from observability.root_cause_analyzer import (
    AnomalyInput,
    CircuitBreakerInput,
    DriftInput,
    RootCauseAnalyzer,
    RootCauseReport,
)

logger = logging.getLogger(__name__)

DEFAULT_ALERT_COOLDOWN_SEC = 1800.0     # 30분
_MAX_RECENT_REPORTS = 100


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        logger.warning(f"{name} 값이 숫자가 아님 → 기본값 {default} 사용")
        return default


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        logger.warning(f"{name} 값이 정수가 아님 → 기본값 {default} 사용")
        return default


# 프로세스 내 최신 스냅샷 캐시 — 대시보드(report/html_dashboard.py)가
# `from observability.ops_monitor import get_ops_snapshot` 으로 읽는다.
# 🔴 2026-10-10 이전에는 이 함수가 없어 ImportError가 무음 삼켜지고
#    대시보드의 관측(품질/튜닝 제안) 섹션이 항상 비어 있었다.
_LATEST_SNAPSHOT: Optional[Dict[str, Any]] = None


def get_ops_snapshot() -> Optional[Dict[str, Any]]:
    """마지막으로 생성된 OpsMonitor 스냅샷(없으면 None)."""
    return _LATEST_SNAPSHOT


class OpsMonitor:
    """이상탐지·드리프트·근본원인 분석을 묶은 관측 파사드.

    Args:
        enabled: False면 모든 관측을 건너뛴다(오버헤드 0).
        anomaly_window: 이상탐지 슬라이딩 윈도우 크기.
        anomaly_threshold: 이상 판정 임계값(0~1).
        drift_window: 드리프트 감지 윈도우 크기.
        alert_cooldown_sec: 알림 최소 간격(초).
        on_alert: 알림 콜백. 동기 함수 또는 코루틴 함수.
        cb_provider: 서킷브레이커 상태 공급자(선택). RCA 정확도 향상용.
    """

    def __init__(
        self,
        enabled: bool = True,
        anomaly_window: int = 200,
        anomaly_threshold: float = 0.65,
        drift_window: int = 200,
        alert_cooldown_sec: float = DEFAULT_ALERT_COOLDOWN_SEC,
        on_alert: Optional[Callable[[str], Any]] = None,
        cb_provider: Optional[Callable[[], CircuitBreakerInput]] = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._alert_cooldown = float(alert_cooldown_sec)
        self._on_alert = on_alert
        self._cb_provider = cb_provider
        self._last_alert_at: float = 0.0

        # 임계/윈도우는 환경변수로 조정 가능(알림 품질 튜닝용, P7)
        self._anomaly_window = _env_int("OPS_ANOMALY_WINDOW", anomaly_window)
        self._anomaly_threshold = _env_float("OPS_ANOMALY_THRESHOLD", anomaly_threshold)
        self._drift_window = _env_int("OPS_DRIFT_WINDOW", drift_window)
        if alert_cooldown_sec == DEFAULT_ALERT_COOLDOWN_SEC:
            self._alert_cooldown = _env_float(
                "OPS_ALERT_COOLDOWN_SEC", DEFAULT_ALERT_COOLDOWN_SEC
            )

        self._anomaly = (
            AnomalyDetector(
                window_size=self._anomaly_window, threshold=self._anomaly_threshold
            )
            if self._enabled else None
        )
        self._drift = (
            ModelDriftDetector(window_size=self._drift_window) if self._enabled else None
        )
        self._rca = RootCauseAnalyzer() if self._enabled else None

        self._recent: Deque[RootCauseReport] = deque(maxlen=_MAX_RECENT_REPORTS)
        self._stats: Dict[str, Any] = {
            "signals_observed": 0,
            "anomalies": 0,
            "drift_reports": 0,
            "reports": 0,
            "alerts_sent": 0,
            "alerts_suppressed": 0,
            "errors": 0,
        }

    # ── 공개 API ────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_cb_provider(self, provider: Optional[Callable[[], CircuitBreakerInput]]) -> None:
        """서킷브레이커 상태 공급자를 설정한다."""
        self._cb_provider = provider

    def set_alert_callback(self, on_alert: Optional[Callable[[str], Any]]) -> None:
        """알림 콜백을 설정한다(부트스트랩에서 텔레그램 주입)."""
        self._on_alert = on_alert

    def record_signal(
        self,
        *,
        score: float,
        confidence: float,
        latency_ms: float,
        sqi: float,
        ticker: str = "",
    ) -> Optional[RootCauseReport]:
        """신호 1건을 관측한다. 이상 감지 시에만 RCA 리포트를 반환한다."""
        if not self._enabled or self._anomaly is None:
            return None
        try:
            self._stats["signals_observed"] += 1
            anomaly: Optional[AnomalyReport] = self._anomaly.observe(
                score, confidence, latency_ms, sqi
            )
            if anomaly is None:
                return None

            self._stats["anomalies"] += 1
            report = self._rca.analyze(  # type: ignore[union-attr]
                anomaly=AnomalyInput(
                    is_anomaly=bool(anomaly.is_anomaly),
                    anomaly_score=float(anomaly.anomaly_score),
                    signal_score=float(score),
                    latency_ms=float(latency_ms),
                    description=f"{ticker} {anomaly.reason}".strip(),
                ),
                cb=self._cb_snapshot(),
            )
            return self._handle_report(report)
        except Exception as e:  # 관측 실패가 매매를 막아선 안 된다
            self._stats["errors"] += 1
            logger.warning(f"OpsMonitor.record_signal 실패: {e}")
            return None

    def record_outcome(
        self,
        strategy_name: str,
        prediction: float,
        outcome: bool,
    ) -> Optional[RootCauseReport]:
        """전략 예측/결과를 관측한다. 드리프트 감지 시에만 RCA 리포트를 반환한다."""
        if not self._enabled or self._drift is None:
            return None
        try:
            drift: Optional[DriftReport] = self._drift.observe(
                strategy_name, prediction, outcome
            )
            if drift is None:
                return None

            self._stats["drift_reports"] += 1
            report = self._rca.analyze(  # type: ignore[union-attr]
                drift=DriftInput(
                    strategy_name=drift.strategy_name,
                    drift_level=str(getattr(drift.drift_level, "value", drift.drift_level)),
                    psi_score=float(drift.psi_score),
                    win_rate_drop=float(drift.win_rate_drop),
                    should_retrain=bool(drift.should_retrain),
                ),
                cb=self._cb_snapshot(),
            )
            return self._handle_report(report)
        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"OpsMonitor.record_outcome 실패: {e}")
            return None

    def snapshot(self) -> Dict[str, Any]:
        """현재 관측 상태 요약(헬스 리포트/대시보드용)."""
        last = self._recent[-1].to_dict() if self._recent else None
        snap = {
            "enabled": self._enabled,
            "stats": dict(self._stats),
            "last_report": last,
            "recent_count": len(self._recent),
            "quality": self.quality_snapshot(),
            "tuning": self.suggest_tuning(),
        }
        # 🔴 대시보드(report/html_dashboard.py)가 모듈 함수 get_ops_snapshot()을
        #   import 하는데 존재하지 않아 관측 섹션이 무음으로 비어 있었다.
        global _LATEST_SNAPSHOT
        _LATEST_SNAPSHOT = snap
        return snap

    def suggest_tuning(self) -> Dict[str, Any]:
        """관측 통계로부터 임계/쿨다운 조정 제안을 만든다 (P10-2).

        판정 규칙(보수적 — 자동 적용하지 않고 **제안만** 한다):
          - 억제율 > 0.5         → 쿨다운 단축 또는 임계 상향
          - 이상 감지율 > 0.10    → 임계 상향(오탐 과다)
          - 감지율 0 & 관측 > 500 → 임계 하향(미탐 의심)
          - 오류율 > 0.01        → 입력 데이터 점검
        """
        q = self.quality_snapshot()
        suggestions: List[Dict[str, Any]] = []
        th = q["thresholds"]

        if q["suppression_ratio"] > 0.5:
            suggestions.append({
                "target": "OPS_ALERT_COOLDOWN_SEC",
                "current": th["alert_cooldown_sec"],
                "suggested": max(300.0, round(th["alert_cooldown_sec"] * 0.5, 1)),
                "reason": f"알림 억제율 {q['suppression_ratio']:.0%} (쿨다운 과다)",
            })
        if q["anomaly_rate"] > 0.10:
            suggestions.append({
                "target": "OPS_ANOMALY_THRESHOLD",
                "current": th["anomaly_threshold"],
                "suggested": round(min(0.95, th["anomaly_threshold"] + 0.05), 3),
                "reason": f"이상 감지율 {q['anomaly_rate']:.0%} (오탐 의심)",
            })
        if q["anomaly_rate"] == 0.0 and q["signals_observed"] > 500:
            suggestions.append({
                "target": "OPS_ANOMALY_THRESHOLD",
                "current": th["anomaly_threshold"],
                "suggested": round(max(0.4, th["anomaly_threshold"] - 0.05), 3),
                "reason": f"관측 {q['signals_observed']}건인데 감지 0건 (미탐 의심)",
            })
        if q["errors"] and q["errors"] / max(1, q["signals_observed"]) > 0.01:
            suggestions.append({
                "target": "입력 데이터",
                "current": q["errors"],
                "suggested": 0,
                "reason": "관측 오류율 1% 초과 — 피처 입력 점검",
            })

        return {
            "sample": q["signals_observed"],
            "applied": False,
            "suggestions": suggestions,
        }

    def recent_reports(self, limit: int = 10) -> list:
        return [r.to_dict() for r in list(self._recent)[-limit:]]

    def quality_snapshot(self) -> Dict[str, Any]:
        """알림 품질 지표 (P7: 과다알림/오탐 추세 파악용).

        Returns:
            관측수·이상감지율·알림률·억제율·오류율과 해석 힌트.
        """
        s = self._stats
        observed = max(1, int(s["signals_observed"]))
        reports = int(s["reports"])
        alerts = int(s["alerts_sent"])
        suppressed = int(s["alerts_suppressed"])
        decided = alerts + suppressed

        anomaly_rate = int(s["anomalies"]) / observed
        alert_rate = (alerts / reports) if reports else 0.0
        suppression_ratio = (suppressed / decided) if decided else 0.0
        error_rate = int(s["errors"]) / observed

        hints: list = []
        if suppression_ratio > 0.5:
            hints.append("알림 억제 비율 높음 → 쿨다운 단축 또는 임계 상향 검토")
        if anomaly_rate > 0.10:
            hints.append("이상 감지율 높음 → 임계(threshold) 상향 검토")
        if anomaly_rate == 0.0 and int(s["signals_observed"]) > 500:
            hints.append("이상 감지 0건 → 임계 하향 검토")
        if error_rate > 0.01:
            hints.append("관측 오류율 높음 → 입력 데이터 확인")

        return {
            "signals_observed": s["signals_observed"],
            "anomalies": s["anomalies"],
            "anomaly_rate": round(anomaly_rate, 4),
            "reports": reports,
            "alerts_sent": alerts,
            "alert_rate": round(alert_rate, 4),
            "alerts_suppressed": suppressed,
            "suppression_ratio": round(suppression_ratio, 4),
            "errors": s["errors"],
            "thresholds": {
                "anomaly_window": self._anomaly_window,
                "anomaly_threshold": self._anomaly_threshold,
                "drift_window": self._drift_window,
                "alert_cooldown_sec": self._alert_cooldown,
            },
            "hints": hints,
        }

    # ── 내부 ────────────────────────────────────────────────────

    def _cb_snapshot(self) -> Optional[CircuitBreakerInput]:
        if self._cb_provider is None:
            return None
        try:
            return self._cb_provider()
        except Exception as e:
            logger.debug(f"CB 스냅샷 실패(무시): {e}")
            return None

    def _handle_report(self, report: RootCauseReport) -> RootCauseReport:
        self._stats["reports"] += 1
        self._recent.append(report)

        if not (report.requires_action or report.is_critical):
            logger.debug(
                f"[OPS] {report.primary_cause.value} sev={report.severity} "
                f"(조치 불필요)"
            )
            return report

        now = time.time()
        if now - self._last_alert_at < self._alert_cooldown:
            self._stats["alerts_suppressed"] += 1
            logger.info(
                f"[OPS] {report.severity} {report.primary_cause.value} "
                f"알림 억제(쿨다운 {self._alert_cooldown:.0f}s)"
            )
            return report

        self._last_alert_at = now
        self._stats["alerts_sent"] += 1
        self._dispatch(self._format_alert(report))
        return report

    def _format_alert(self, report: RootCauseReport) -> str:
        evidence = " / ".join(str(e) for e in (report.evidence or [])[:3]) or "-"
        return (
            "🩺 <b>시스템 이상 감지</b>\n"
            f"• 원인: <b>{report.primary_cause.value}</b> (신뢰도 {report.confidence:.2f})\n"
            f"• 심각도: {report.severity}\n"
            f"• 권장 조치: {report.recommended_action.value}\n"
            f"• 근거: {evidence}"
        )

    def _dispatch(self, message: str) -> None:
        cb = self._on_alert
        if cb is None:
            logger.warning(f"[OPS][알림 미배선] {message}")
            return
        try:
            if asyncio.iscoroutinefunction(cb):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    logger.warning("[OPS] 이벤트 루프 없음 → 알림 생략")
                    return
                loop.create_task(self._safe_alert(cb, message))
            else:
                cb(message)
        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"OpsMonitor 알림 전송 실패: {e}")

    async def _safe_alert(self, cb: Callable[[str], Awaitable[Any]], message: str) -> None:
        try:
            await cb(message)
        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"OpsMonitor 비동기 알림 실패: {e}")
