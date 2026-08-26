# -*- coding: utf-8 -*-
"""
Calibration Tracker v5.2.0
Confidence Calibration Drift 감지 (Regime별 분리) + ABTest 연동

변경 이력:
    v5.2.0  - ABTestManager 연동: regime별 calibration 결과를 A/B 실험에 피드백
            - @trace.traced 적용: record / get_calibration
            - record_ab_result() 신규: ECE 기반 calibration 품질을 A/B 메트릭으로 전송
    v5.1.2  - 초기 버전 (CRLF → LF 변환 완료)
"""

from collections import defaultdict
from datetime import datetime
from typing import Optional

from core.logger import setup_logger
from observability.tracer import get_tracer

logger = setup_logger("calibration")
trace = get_tracer(__name__)

# A/B 테스트 이름 (bootstrap에서 등록된 실험 이름과 일치)
_AB_CALIBRATION_TEST = "calibration_quality"


class CalibrationTracker:
    """Calibration 추적기 (Regime × Confidence 교차) + ABTest 연동.

    사용 예::

        tracker = CalibrationTracker()

        # 예측 결과 기록
        tracker.record(regime="trend", confidence=0.82, actual_win=True)

        # Calibration 품질 조회
        cal = tracker.get_calibration("trend")  # {"ece": 0.03, "status": "PASS", ...}

        # ABTest에 ECE 메트릭 피드백 (선택)
        await tracker.record_ab_result("trend", cal)
    """

    def __init__(self, ab_manager=None) -> None:
        """
        Args:
            ab_manager: ABTestManager 인스턴스 (None이면 lazy import로 전역 싱글톤 사용)
        """
        self.data: dict[str, list[dict]] = defaultdict(list)   # regime별 저장
        self._ab_manager = ab_manager

    def _get_ab_manager(self):
        """Lazy import: 순환 임포트 방지."""
        if self._ab_manager is None:
            try:
                from application.analysis.ab_framework import get_ab_manager
                self._ab_manager = get_ab_manager()
            except Exception as e:
                logger.debug("CalibrationTracker: ABTestManager 로드 실패 (무시) %s", e)
        return self._ab_manager

    @trace.traced
    def record(self, regime: str, confidence: float, actual_win: bool) -> None:
        """Calibration 데이터 기록.

        Args:
            regime: 시장 regime 식별자 (e.g., "trend", "reversal", "sideways")
            confidence: 모델 예측 신뢰도 (0~1)
            actual_win: 실제 예측 결과 (True=맞춤, False=틀림)
        """
        self.data[regime].append(
            {
                "confidence": confidence,
                "actual_win": actual_win,
                "timestamp": datetime.now().isoformat(),
            }
        )

    @trace.traced
    def get_calibration(self, regime: str) -> dict:
        """Regime별 Calibration 계산.

        Returns:
            dict with keys:
                - status: "PASS" | "WARN" | "insufficient_data"
                - ece: Expected Calibration Error (0~1, 낮을수록 좋음)
                - buckets: 구간별 승률 vs 기대 신뢰도
                - regime: 입력 regime 값
                - sample: 총 샘플 수 (insufficient 시)
        """
        records = self.data.get(regime, [])
        if len(records) < 10:
            return {"status": "insufficient_data", "sample": len(records)}

        # Confidence 구간별 승률 계산
        buckets = [
            (0.90, 1.00, []),
            (0.80, 0.89, []),
            (0.70, 0.79, []),
            (0.60, 0.69, []),
            (0.00, 0.59, []),
        ]

        for rec in records:
            conf = rec["confidence"]
            for low, high, bucket in buckets:
                if low <= conf <= high:
                    bucket.append(rec["actual_win"])
                    break

        result = {}
        for low, high, bucket in buckets:
            if bucket:
                win_rate = sum(bucket) / len(bucket)
                result[f"{low:.0%}-{high:.0%}"] = {
                    "sample": len(bucket),
                    "win_rate": win_rate,
                    "expected": (low + high) / 2,
                }

        # ECE 계산
        ece = 0.0
        total_samples = sum(v["sample"] for v in result.values())
        if total_samples > 0:
            for _bucket, data in result.items():
                ece += (data["sample"] / total_samples) * abs(
                    data["win_rate"] - data["expected"]
                )

        status = "PASS" if ece < 0.05 else "WARN"
        return {
            "regime": regime,
            "ece": round(ece, 6),
            "buckets": result,
            "status": status,
            "total_samples": total_samples,
        }

    @trace.traced
    async def record_ab_result(
        self, regime: str, calibration_result: Optional[dict] = None
    ) -> bool:
        """Calibration 품질을 ABTest에 피드백.

        ECE(Expected Calibration Error)를 A/B 테스트 메트릭으로 전송합니다.
        낮은 ECE = 높은 calibration 품질 → 음수 변환하여 "높을수록 좋음" 규칙 적용.

        메트릭 변환:
            ab_metric = 1.0 - ece   (ECE=0.0 → 1.0 최고, ECE=1.0 → 0.0 최저)

        Args:
            regime: regime 이름 (A/B 변형 이름으로 사용)
            calibration_result: get_calibration() 결과 (None이면 자동 계산)

        Returns:
            True if 기록 성공, False otherwise
        """
        manager = self._get_ab_manager()
        if manager is None:
            return False

        # 기존 calibration 결과 사용 또는 신규 계산
        cal = calibration_result or self.get_calibration(regime)
        if cal.get("status") == "insufficient_data":
            logger.debug(
                "CalibrationTracker.record_ab_result: regime='%s' 샘플 부족 — 건너뜀",
                regime,
            )
            return False

        ece = cal.get("ece", 1.0)
        ab_metric = 1.0 - ece   # ECE → calibration quality score (높을수록 좋음)

        # regime 이름을 variant로 사용 (e.g., "trend", "reversal")
        # ABTest "calibration_quality"가 존재하는 경우에만 기록
        test = manager._tests.get(_AB_CALIBRATION_TEST)
        if test is None:
            logger.debug(
                "CalibrationTracker: ABTest '%s' 없음 — 건너뜀", _AB_CALIBRATION_TEST
            )
            return False

        # 변형 이름이 테스트에 등록된 경우에만 기록
        if regime not in test.variants:
            logger.debug(
                "CalibrationTracker: regime='%s'이 '%s' 변형 목록에 없음 — 건너뜀",
                regime,
                _AB_CALIBRATION_TEST,
            )
            return False

        await manager.record_result(_AB_CALIBRATION_TEST, regime, ab_metric)
        logger.info(
            "CalibrationTracker → ABTest[%s]: regime='%s' ECE=%.4f → ab_metric=%.4f",
            _AB_CALIBRATION_TEST,
            regime,
            ece,
            ab_metric,
        )
        return True

    def get_all_regimes(self) -> list[str]:
        """데이터가 있는 전체 regime 목록 반환."""
        return list(self.data.keys())

    def get_sample_counts(self) -> dict[str, int]:
        """Regime별 샘플 수 요약."""
        return {regime: len(records) for regime, records in self.data.items()}
