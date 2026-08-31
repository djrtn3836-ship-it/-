"""
Safety Guard v5.2.0 — 방향성 비교 재설계 + 이상치 방어 + 알림 쿨다운 내재화

v5.1.3 → v5.2.0 변경 사항:
    - CRITICAL: _is_triggered()의 abs() 비교로 인한 방향 무시 버그 수정
      (KOSPI 급등을 폭락으로 오판, kospi_drop=+1071.85 같은 값도 트리거되던 문제)
      → 조건별 의미에 맞는 방향성 비교로 전면 재설계
    - usdkrw_spike 임계값 1350 → 1400 상향 (CONTEXT.md v8.0.0 설계 의도 복원)
    - 이상치(plausible range) 방어 추가: 데이터 수집 오류로 비정상적인 값이
      들어와도 오탐하지 않도록 조건별 타당 범위를 사전 필터링.
      이 범위는 "물리적으로 말이 되는 값인가"만 판단하며, "위기인지 여부"(방향성)는
      의도적으로 _is_triggered()에만 맡겨 책임을 분리함
    - 알림 쿨다운(alert_cooldown_sec) 및 차단 해제 감지(block_cleared)를
      SafetyGuard 내부로 내재화. app/bootstrap.py에 임시로 추가했던
      알림 스팸 방지 로직을 이곳으로 옮겨 책임을 일원화함
    - 알 수 없는 조건에 대한 fallback을 abs() 비교 대신 False로 변경
      (미지의 조건에서 동일한 유형의 버그가 재발하는 것을 원천 차단)
"""

import logging
import time
from dataclasses import dataclass

from observability.tracer import get_tracer

logger = logging.getLogger(__name__)
trace = get_tracer(__name__)


@dataclass
class SafetyThreshold:
    """안전 장치 임계값 (근거 포함)"""

    value: float
    basis: str          # 근거 설명
    source: str          # 데이터 출처
    confidence: float = 0.95   # 신뢰수준


# 조건별 타당 입력 범위. 이 범위를 벗어나면 데이터 수집 오류로 간주하고
# 트리거 판정 자체를 건너뜁니다 (오탐 방지의 1차 방어선).
# 방향성(위기 여부) 판단은 여기서 하지 않고 _is_triggered()에 위임합니다.
_PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "kospi_drop": (-30.0, 30.0),
    "vkospi_spike": (0.0, 150.0),
    "usdkrw_spike": (500.0, 3000.0),
    "feature_expired": (0.0, 100.0),
    "tr_latency": (0.0, 120000.0),
    "calibration_error": (0.0, 100.0),
}


class SafetyGuard:
    """
    안전 장치 v5.2.0

    각 임계값은 한국 시장 실제 데이터 기반으로 산출
    """

    THRESHOLDS = {
        "kospi_drop": SafetyThreshold(
            value=-3.0,
            basis="KOSPI 5일 수익률 -3% 이하 급락 시 (일평균 변동성 1.2% × 2.5)",
            source="KOSPI 2020-2026 일별 수익률 데이터, σ=1.2%",
        ),
        "vkospi_spike": SafetyThreshold(
            value=30.0,
            basis="VKOSPI 평균 18.5 + 1.5σ (표준편차 7.5, 상위 6.7% 구간)",
            source="VKOSPI 2020-2026 일별 데이터, μ=18.5, σ=7.5",
        ),
        "usdkrw_spike": SafetyThreshold(
            value=1400.0,
            basis="USDKRW 1400원 상향 돌파 시 위기 (2022/2024년 실제 고점대 반영, CONTEXT.md v8.0.0 설계 복원)",
            source="USDKRW 2020-2026 일별 데이터 + 운영 경험치",
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

    _CRITICAL_CONDITIONS = ("kospi_drop", "vkospi_spike", "usdkrw_spike")

    def __init__(self, alert_cooldown_sec: float = 1800.0) -> None:
        """
        Args:
            alert_cooldown_sec: BLOCK_ALL 알림 최소 재전송 간격(초). 기본 30분.
                                이전에 app/bootstrap.py에 임시로 두었던 쿨다운
                                로직을 이곳으로 이관했습니다.
        """
        self._trigger_log: list[dict] = []
        self._alert_cooldown_sec = alert_cooldown_sec
        self._last_alert_time: float = 0.0
        self._last_block_time: float = 0.0
        self._was_blocked: bool = False

    @trace.traced
    def check(self, data: dict) -> dict:
        """
        모든 안전 조건 체크

        Returns:
            {
                'all_clear': bool,
                'triggered': list,
                'action': 'BLOCK_ALL' | 'WARNING' | 'NONE',
                'trigger_count': int,
                'critical_triggered': bool,
                'should_alert': bool,    # 이번 체크에서 알림을 보내야 하는지 (쿨다운 반영)
                'block_cleared': bool,   # 이번 체크에서 차단이 해제됐는지
            }
        """
        triggered: list[dict] = []

        for condition, threshold in self.THRESHOLDS.items():
            current = data.get(condition)
            if current is None:
                continue

            # 1차 방어: 물리적으로 타당하지 않은 값은 위기 판정 자체를 건너뜀
            plausible = _PLAUSIBLE_RANGES.get(condition)
            if plausible is not None:
                lo, hi = plausible
                if not (lo <= current <= hi):
                    logger.debug(
                        f"[SafetyGuard] {condition} 이상치 무시: "
                        f"{current} (타당 범위: {lo}~{hi})"
                    )
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
        action = "BLOCK_ALL" if has_critical else ("WARNING" if triggered else "NONE")

        # ─── 알림 쿨다운 / 차단 해제 판정 ──────────────────────────
        now = time.time()
        should_alert = False
        block_cleared = False

        if action == "BLOCK_ALL":
            self._last_block_time = now
            if now - self._last_alert_time >= self._alert_cooldown_sec:
                should_alert = True
                self._last_alert_time = now
            else:
                remaining = self._alert_cooldown_sec - (now - self._last_alert_time)
                logger.debug(f"[SafetyGuard] 알림 쿨다운 중 (잔여 {remaining:.0f}초)")
            self._was_blocked = True
        else:
            if self._was_blocked:
                block_cleared = True
                should_alert = True   # 해제 알림은 쿨다운 무시하고 즉시 발송
                self._last_alert_time = 0.0
                self._was_blocked = False
                logger.info("[SafetyGuard] 차단 해제됨 — 정상 운영 재개")

        return {
            "all_clear": len(triggered) == 0,
            "triggered": triggered,
            "action": action,
            "trigger_count": len(triggered),
            "critical_triggered": has_critical,
            "should_alert": should_alert,
            "block_cleared": block_cleared,
        }

    @trace.traced
    def _is_triggered(self, condition: str, current: float, threshold: SafetyThreshold) -> bool:
        """
        조건별 트리거 판정 — 의미에 맞는 방향성 비교.

        kospi_drop: '하락'이므로 current <= threshold.value (음수 방향)
        vkospi_spike, usdkrw_spike: '급등'이므로 current >= threshold.value
        feature_expired, tr_latency, calibration_error: '초과'이므로 current >= threshold.value
        알 수 없는 조건: abs() 비교로 회귀하지 않고 안전하게 미차단(False) 처리
        """
        if condition == "kospi_drop":
            return current <= threshold.value

        if condition in ("vkospi_spike", "usdkrw_spike"):
            return current >= threshold.value

        if condition in ("feature_expired", "tr_latency", "calibration_error"):
            return current >= threshold.value

        logger.warning(f"[SafetyGuard] 알 수 없는 조건 '{condition}' — 안전을 위해 미차단 처리")
        return False

    def _get_severity(self, condition: str) -> str:
        """심각도 판정"""
        return "CRITICAL" if condition in self._CRITICAL_CONDITIONS else "HIGH"

    @trace.traced
    def get_threshold_basis(self) -> dict:
        """임계값 근거 요약 반환"""
        return {
            condition: {
                "value": th.value,
                "basis": th.basis,
                "source": th.source,
                "confidence": th.confidence,
            }
            for condition, th in self.THRESHOLDS.items()
        }

    @trace.traced
    def get_trigger_log(self, limit: int = 50) -> list[dict]:
        """최근 트리거 이력 반환 (모니터링/디버깅용)"""
        return self._trigger_log[-limit:]

    def get_status(self) -> dict:
        """헬스체크용 상태 반환"""
        return {
            "was_blocked": self._was_blocked,
            "last_block_time": self._last_block_time,
            "last_alert_time": self._last_alert_time,
            "alert_cooldown_sec": self._alert_cooldown_sec,
            "trigger_log_count": len(self._trigger_log),
        }
