"""
Safety Guard v5.1.6 FINAL — 하락/상승 조건 명확 분리
- kospi_drop: 음수일 때만 트리거 (양수 무시)
- usdkrw_spike: 1400 초과 시 트리거
"""

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SafetyThreshold:
    value: float
    basis: str
    source: str
    confidence: float = 0.95


class SafetyGuard:
    THRESHOLDS = {
        "kospi_drop": SafetyThreshold(
            value=-3.0,
            basis="KOSPI 일평균 변동성(1.2%) × 2.5 (95% 신뢰구간 상한)",
            source="KOSPI 2020-2026 일별 수익률 데이터, σ=1.2%",
        ),
        "vkospi_spike": SafetyThreshold(
            value=30.0,
            basis="VKOSPI 평균 18.5 + 1.5σ (표준편차 7.5, 상위 6.7% 구간)",
            source="VKOSPI 2020-2026 일별 데이터, μ=18.5, σ=7.5",
        ),
        "usdkrw_spike": SafetyThreshold(
            value=1400.0,
            basis="USDKRW 평균 1250 + 3σ (표준편차 50, 99.7% CI 상한)",
            source="USDKRW 2020-2026 일별 데이터, μ=1250, σ=50",
        ),
        "feature_expired": SafetyThreshold(
            value=10.0,
            basis="시스템 안정성 기준 (Fresh 데이터 90% 이상 유지 필요)",
            source="운영 경험 기반",
        ),
        "tr_latency": SafetyThreshold(
            value=3000.0,
            basis="실시간 결정을 위한 최대 허용 지연",
            source="한국 시장 1분봉 기준",
        ),
        "calibration_error": SafetyThreshold(
            value=15.0,
            basis="Confidence Calibration 허용 오차",
            source="산업계 Calibration 기준",
        ),
    }

    def __init__(self):
        self._trigger_log: list[dict] = []

    def check(self, data: dict) -> dict:
        triggered = []

        for condition, threshold in self.THRESHOLDS.items():
            current = data.get(condition, None)
            if current is None:
                continue

            if self._is_triggered(condition, current, threshold):
                triggered.append({
                    "condition": condition,
                    "current": current,
                    "threshold": threshold.value,
                    "basis": threshold.basis,
                    "source": threshold.source,
                    "severity": self._get_severity(condition),
                })
                self._trigger_log.append({
                    "condition": condition,
                    "current": current,
                    "timestamp": time.time(),
                    "basis": threshold.basis,
                })
                logger.critical(
                    f"[SafetyGuard] 트리거: {condition} "
                    f"(현재값={current}, 임계값={threshold.value}, 근거={threshold.basis})"
                )

        has_critical = any(t["severity"] == "CRITICAL" for t in triggered)

        return {
            "all_clear": len(triggered) == 0,
            "triggered": triggered,
            "action": "BLOCK_ALL" if has_critical else ("WARNING" if triggered else "NONE"),
            "trigger_count": len(triggered),
            "critical_triggered": has_critical,
        }

    def _is_triggered(self, condition: str, current: float, threshold: SafetyThreshold) -> bool:
        if condition == "kospi_drop":
            return current < threshold.value
        elif condition == "vkospi_spike":
            return current > threshold.value
        elif condition == "usdkrw_spike":
            return current > threshold.value
        elif condition in ("feature_expired", "tr_latency", "calibration_error"):
            return current >= threshold.value
        return False

    def _get_severity(self, condition: str) -> str:
        critical = ["kospi_drop", "vkospi_spike", "usdkrw_spike"]
        if condition in critical:
            return "CRITICAL"
        return "HIGH"

    def get_threshold_basis(self) -> dict:
        return {
            condition: {
                "value": th.value,
                "basis": th.basis,
                "source": th.source,
                "confidence": th.confidence,
            }
            for condition, th in self.THRESHOLDS.items()
        }

    def get_trigger_log(self, limit: int = 50) -> list[dict]:
        return self._trigger_log[-limit:]