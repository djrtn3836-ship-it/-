"""
observability/model_drift_detector.py - v1.0 (Session 10)

Model Drift Detection: 전략 예측 성능 저하 자동 감지
- PSI (Population Stability Index): 분포 변화 감지
- KS Statistic: 두 분포 차이 (순수 Python)
- WinRateTracker: 슬라이딩 윈도우 승률 추적
- should_retrain: PSI>0.20 or 승률 하락 >10%p → 재학습 권고
"""

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

from core.logger import setup_logger

logger = setup_logger("model_drift_detector")

_PSI_WARNING_THRESHOLD = 0.10
_PSI_DRIFT_THRESHOLD = 0.20
_WIN_RATE_DROP_THRESHOLD = 0.10
_DEFAULT_WINDOW_SIZE = 30
_MIN_SAMPLES_FOR_DRIFT = 20


class DriftLevel(str, Enum):
    """드리프트 수준"""
    STABLE = "STABLE"
    WARNING = "WARNING"
    DRIFT = "DRIFT"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class DriftReport:
    """드리프트 분석 리포트"""
    strategy_name: str
    drift_level: DriftLevel
    psi_score: float
    ks_statistic: float
    win_rate_current: float
    win_rate_baseline: float
    win_rate_drop: float
    should_retrain: bool
    drift_reason: str
    sample_count: int
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "drift_level": self.drift_level.value,
            "psi_score": round(self.psi_score, 4),
            "ks_statistic": round(self.ks_statistic, 4),
            "win_rate_current": round(self.win_rate_current, 4),
            "win_rate_baseline": round(self.win_rate_baseline, 4),
            "win_rate_drop": round(self.win_rate_drop, 4),
            "should_retrain": self.should_retrain,
            "drift_reason": self.drift_reason,
            "sample_count": self.sample_count,
            "timestamp": self.timestamp.isoformat(),
        }


def compute_psi(
    expected: List[float],
    actual: List[float],
    bins: int = 10,
) -> float:
    """
    Population Stability Index (PSI) 계산.

    PSI = Σ (actual_% - expected_%) × ln(actual_% / expected_%)
    - PSI < 0.10 → 안정 (STABLE)
    - PSI 0.10~0.20 → 주의 (WARNING)
    - PSI > 0.20 → 드리프트 (DRIFT)
    """
    if len(expected) < 2 or len(actual) < 2:
        return 0.0

    min_val = 0.0
    max_val = 1.0
    bin_width = (max_val - min_val) / bins

    def count_in_bins(values: List[float]) -> List[int]:
        counts = [0] * bins
        for v in values:
            idx = int((v - min_val) / bin_width)
            idx = max(0, min(bins - 1, idx))
            counts[idx] += 1
        return counts

    exp_counts = count_in_bins(expected)
    act_counts = count_in_bins(actual)

    n_exp = len(expected)
    n_act = len(actual)
    _EPSILON = 1e-6

    psi = 0.0
    for i in range(bins):
        exp_pct = max(exp_counts[i] / n_exp, _EPSILON)
        act_pct = max(act_counts[i] / n_act, _EPSILON)
        psi += (act_pct - exp_pct) * math.log(act_pct / exp_pct)

    return max(0.0, psi)


def compute_ks_statistic(
    a: List[float],
    b: List[float],
) -> Tuple[float, float]:
    """
    Kolmogorov-Smirnov 통계량 계산 (순수 Python).
    두 분포의 CDF 최대 차이를 구함. p-value는 Kolmogorov 분포 근사 사용.
    """
    if len(a) < 2 or len(b) < 2:
        return 0.0, 1.0

    n_a = len(a)
    n_b = len(b)
    combined = sorted(set(a + b))

    max_diff = 0.0
    for x in combined:
        cdf_a = sum(1 for v in a if v <= x) / n_a
        cdf_b = sum(1 for v in b if v <= x) / n_b
        diff = abs(cdf_a - cdf_b)
        if diff > max_diff:
            max_diff = diff

    en = math.sqrt(n_a * n_b / (n_a + n_b))
    lambda_val = (en + 0.12 + 0.11 / en) * max_diff

    if lambda_val <= 0:
        p_value = 1.0
    else:
        p_value = 2.0 * sum(
            ((-1) ** (k - 1)) * math.exp(-2.0 * k * k * lambda_val ** 2)
            for k in range(1, 4)
        )
        p_value = max(0.0, min(1.0, p_value))

    return max_diff, p_value


class _WinRateTracker:
    """슬라이딩 윈도우 승률 추적기 (내부 헬퍼)"""

    def __init__(self, window_size: int = _DEFAULT_WINDOW_SIZE):
        self._window: deque = deque(maxlen=window_size)
        self._baseline_win_rate: Optional[float] = None
        self._baseline_sample_count: int = 0

    def record(self, win: bool) -> None:
        self._window.append(1.0 if win else 0.0)

    @property
    def current_win_rate(self) -> float:
        if not self._window:
            return 0.0
        return sum(self._window) / len(self._window)

    @property
    def baseline_win_rate(self) -> float:
        return self._baseline_win_rate if self._baseline_win_rate is not None else 0.0

    def set_baseline(self, win_rate: float, sample_count: int) -> None:
        self._baseline_win_rate = win_rate
        self._baseline_sample_count = sample_count

    def auto_set_baseline(self) -> bool:
        if self._baseline_win_rate is None and len(self._window) >= _MIN_SAMPLES_FOR_DRIFT:
            self._baseline_win_rate = self.current_win_rate
            self._baseline_sample_count = len(self._window)
            return True
        return False

    @property
    def win_rate_drop(self) -> float:
        if self._baseline_win_rate is None:
            return 0.0
        return self.current_win_rate - self._baseline_win_rate

    @property
    def sample_count(self) -> int:
        return len(self._window)

    def reset(self) -> None:
        if self._window:
            self._baseline_win_rate = self.current_win_rate
            self._baseline_sample_count = len(self._window)
        self._window.clear()


class ModelDriftDetector:
    """
    전략별 모델 드리프트 감지기.

    각 전략의 예측 확률 분포 변화(PSI)와 승률 추이를 추적하여
    드리프트 여부를 판단하고 재학습 권고를 제공한다.

    사용 예::

        detector = ModelDriftDetector()
        report = detector.observe("trend_strategy", prediction=0.72, outcome=True)
        if report and report.should_retrain:
            logger.warning(f"재학습 필요: {report.drift_reason}")
    """

    def __init__(
        self,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        baseline_size: int = 50,
        psi_bins: int = 10,
    ):
        self._window_size = window_size
        self._baseline_size = baseline_size
        self._psi_bins = psi_bins

        self._pred_windows: Dict[str, deque] = {}
        self._pred_baselines: Dict[str, List[float]] = {}
        self._win_trackers: Dict[str, _WinRateTracker] = {}
        self._recent_reports: deque = deque(maxlen=100)

    def _ensure_strategy(self, strategy_name: str) -> None:
        if strategy_name not in self._pred_windows:
            self._pred_windows[strategy_name] = deque(maxlen=self._window_size)
            self._pred_baselines[strategy_name] = []
            self._win_trackers[strategy_name] = _WinRateTracker(self._window_size)

    def observe(
        self,
        strategy_name: str,
        prediction: float,
        outcome: bool,
    ) -> Optional[DriftReport]:
        try:
            prediction = max(0.0, min(1.0, float(prediction)))
            self._ensure_strategy(strategy_name)

            window = self._pred_windows[strategy_name]
            baseline = self._pred_baselines[strategy_name]
            tracker = self._win_trackers[strategy_name]

            window.append(prediction)

            if len(baseline) < self._baseline_size:
                baseline.append(prediction)
                if len(baseline) == self._baseline_size:
                    tracker.set_baseline(tracker.current_win_rate, tracker.sample_count)
                    logger.info(f"[{strategy_name}] 베이스라인 수집 완료 ({self._baseline_size}샘플)")
                tracker.record(outcome)
                return None

            tracker.record(outcome)
            tracker.auto_set_baseline()

            if len(window) < _MIN_SAMPLES_FOR_DRIFT:
                return None

            psi = compute_psi(baseline, list(window), bins=self._psi_bins)
            ks_stat, _ = compute_ks_statistic(baseline, list(window))

            win_rate_current = tracker.current_win_rate
            win_rate_baseline = tracker.baseline_win_rate
            win_rate_drop = tracker.win_rate_drop

            drift_level, drift_reason, should_retrain = self._classify_drift(
                psi, ks_stat, win_rate_drop, strategy_name
            )

            if drift_level != DriftLevel.STABLE:
                report = DriftReport(
                    strategy_name=strategy_name,
                    drift_level=drift_level,
                    psi_score=psi,
                    ks_statistic=ks_stat,
                    win_rate_current=win_rate_current,
                    win_rate_baseline=win_rate_baseline,
                    win_rate_drop=win_rate_drop,
                    should_retrain=should_retrain,
                    drift_reason=drift_reason,
                    sample_count=len(window),
                )
                self._recent_reports.append(report)

                if drift_level in (DriftLevel.DRIFT, DriftLevel.CRITICAL):
                    logger.warning(
                        f"[{strategy_name}] {drift_level.value} 감지: {drift_reason} "
                        f"(PSI={psi:.3f}, WinRateDrop={win_rate_drop:+.1%})"
                    )
                return report

            return None

        except Exception as e:
            logger.warning(f"observe 실패 [{strategy_name}]: {e}")
            return None

    def _classify_drift(
        self,
        psi: float,
        ks_stat: float,
        win_rate_drop: float,
        strategy_name: str,
    ) -> Tuple[DriftLevel, str, bool]:
        reasons = []
        psi_level = DriftLevel.STABLE
        wr_level = DriftLevel.STABLE

        if psi >= _PSI_DRIFT_THRESHOLD:
            psi_level = DriftLevel.DRIFT
            reasons.append(f"PSI={psi:.3f}(>{_PSI_DRIFT_THRESHOLD})")
        elif psi >= _PSI_WARNING_THRESHOLD:
            psi_level = DriftLevel.WARNING
            reasons.append(f"PSI={psi:.3f}(>{_PSI_WARNING_THRESHOLD})")

        if win_rate_drop <= -_WIN_RATE_DROP_THRESHOLD:
            wr_level = DriftLevel.DRIFT
            reasons.append(f"승률하락={win_rate_drop:+.1%}(>{_WIN_RATE_DROP_THRESHOLD:.0%})")
        elif win_rate_drop <= -_WIN_RATE_DROP_THRESHOLD / 2:
            wr_level = DriftLevel.WARNING
            reasons.append(f"승률소폭하락={win_rate_drop:+.1%}")

        if psi_level == DriftLevel.DRIFT and wr_level == DriftLevel.DRIFT:
            level = DriftLevel.CRITICAL
        elif psi_level == DriftLevel.DRIFT or wr_level == DriftLevel.DRIFT:
            level = DriftLevel.DRIFT
        elif psi_level == DriftLevel.WARNING or wr_level == DriftLevel.WARNING:
            level = DriftLevel.WARNING
        else:
            level = DriftLevel.STABLE

        should_retrain = level in (DriftLevel.DRIFT, DriftLevel.CRITICAL)
        reason = " + ".join(reasons) if reasons else "정상"
        return level, reason, should_retrain

    def psi_score(self, strategy_name: str) -> float:
        try:
            self._ensure_strategy(strategy_name)
            baseline = self._pred_baselines[strategy_name]
            window = list(self._pred_windows[strategy_name])
            if len(baseline) < self._baseline_size or len(window) < _MIN_SAMPLES_FOR_DRIFT:
                return 0.0
            return compute_psi(baseline, window, bins=self._psi_bins)
        except Exception as e:
            logger.warning(f"psi_score 실패 [{strategy_name}]: {e}")
            return 0.0

    def win_rate(self, strategy_name: str) -> float:
        try:
            self._ensure_strategy(strategy_name)
            return self._win_trackers[strategy_name].current_win_rate
        except Exception as e:
            logger.warning(f"win_rate 실패 [{strategy_name}]: {e}")
            return 0.0

    def force_baseline_reset(self, strategy_name: str) -> None:
        try:
            self._ensure_strategy(strategy_name)
            window = list(self._pred_windows[strategy_name])
            if window:
                self._pred_baselines[strategy_name] = window.copy()
                self._win_trackers[strategy_name].reset()
                logger.info(f"[{strategy_name}] 베이스라인 재설정 완료 ({len(window)}샘플)")
        except Exception as e:
            logger.warning(f"force_baseline_reset 실패 [{strategy_name}]: {e}")

    def recent_drifts(self, n: int = 10) -> List[DriftReport]:
        reports = list(self._recent_reports)
        return list(reversed(reports))[:n]

    def strategy_status(self, strategy_name: str) -> dict:
        try:
            self._ensure_strategy(strategy_name)
            baseline = self._pred_baselines[strategy_name]
            window = list(self._pred_windows[strategy_name])
            tracker = self._win_trackers[strategy_name]

            baseline_ready = len(baseline) >= self._baseline_size
            psi = self.psi_score(strategy_name) if baseline_ready else None

            return {
                "strategy_name": strategy_name,
                "baseline_ready": baseline_ready,
                "baseline_samples": len(baseline),
                "window_samples": len(window),
                "psi_score": round(psi, 4) if psi is not None else None,
                "win_rate_current": round(tracker.current_win_rate, 4),
                "win_rate_baseline": round(tracker.baseline_win_rate, 4),
                "win_rate_drop": round(tracker.win_rate_drop, 4),
            }
        except Exception as e:
            logger.warning(f"strategy_status 실패 [{strategy_name}]: {e}")
            return {"strategy_name": strategy_name, "error": str(e)}

    def all_strategy_statuses(self) -> List[dict]:
        return [self.strategy_status(name) for name in self._pred_windows]
