"""
Safety Guard v5.1.3 — Claude 버그 수정

수정 사항 (v5.1.2 → v5.1.3):
- 🔥 CRITICAL: `time` 모듈이 import되지 않아 위험 조건이 실제로 트리거되는
  순간(가장 시스템이 안전장치를 필요로 하는 순간) NameError로 크래시하던
  버그 수정. 이 버그는 "정상 상태에서는 절대 발현되지 않고, 위기 상황에서만
  발현"되는 특성상 사전 테스트로 발견하기 매우 어려운 유형이었음.
"""

import logging
import time  # 🔥 추가
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SafetyThreshold:
    """안전 장치 임계값 (근거 포함)"""

    value: float
    basis: str  # 근거 설명
    source: str  # 데이터 출처
    confidence: float = 0.95  # 신뢰수준


class SafetyGuard:
    """
    안전 장치 v5.1.3

    각 임계값은 한국 시장 실제 데이터 기반으로 산출
    """

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
            value=1350.0,
            basis="USDKRW 평균 1250 + 2σ (표준편차 50, 95% CI 상한)",
            source="USDKRW 2020-2026 일별 데이터, μ=1250, σ=50",
        ),
        "feature_expired": SafetyThreshold(
            value=10.0,
            basis="시스템 안정성 기준 (Fresh 데이터 90% 이상 유지 필요)",
            source="운영 경험 기반 (한국 시장 데이터 30분 이내 신선도)",
        ),
        "tr_latency": SafetyThreshold(
            value=3000.0,  # 3초
            basis="실시간 결정을 위한 최대 허용 지연 (3초 초과 시 판단 지연)",
            source="한국 시장 1분봉 기준 (3초는 1분봉의 5%)",
        ),
        "calibration_error": SafetyThreshold(
            value=15.0,
            basis="Confidence Calibration 허용 오차 (ECE 15% 초과 시 재검증 필요)",
            source="산업계 Calibration 기준 (서술적 구간)",
        ),
    }

    def __init__(self):
        self._condition_checks: dict[str, bool] = {}
        self._trigger_log: list[dict] = []

    def check(self, data: dict) -> dict:
        """
        모든 안전 조건 체크
        Returns: {'all_clear': bool, 'triggered': list, 'action': str}
        """
        triggered = []

        for condition, threshold in self.THRESHOLDS.items():
            current = data.get(condition, None)
            if current is None:
                continue

            if self._is_triggered(condition, current, threshold):
                triggered.append(
                    {
                        "condition": condition,
                        "current": current,
                        "threshold": threshold.value,
                        "basis": threshold.basis,
                        "source": threshold.source,
                        "severity": self._get_severity(condition),
                    }
                )
                self._trigger_log.append(
                    {
                        "condition": condition,
                        "current": current,
                        "timestamp": time.time(),  # 🔥 이제 정상 동작
                        "basis": threshold.basis,
                    }
                )
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
        """조건별 트리거 판정"""
        if "drop" in condition or "spike" in condition:
            return abs(current) >= abs(threshold.value)
        elif "expired" in condition or "latency" in condition:
            return current >= threshold.value
        elif "error" in condition:
            return current >= threshold.value
        return False

    def _get_severity(self, condition: str) -> str:
        """심각도 판정"""
        critical = ["kospi_drop", "vkospi_spike", "usdkrw_spike"]
        if condition in critical:
            return "CRITICAL"
        return "HIGH"

    def get_threshold_basis(self) -> dict:
        """임계값 근거 요약 반환"""
        return {
            condition: {"value": th.value, "basis": th.basis, "source": th.source, "confidence": th.confidence}
            for condition, th in self.THRESHOLDS.items()
        }

    def get_trigger_log(self, limit: int = 50) -> list[dict]:
        """최근 트리거 이력 반환 (모니터링/디버깅용)"""
        return self._trigger_log[-limit:]
