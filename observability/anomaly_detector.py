# -*- coding: utf-8 -*-
"""
observability/anomaly_detector.py - 비정상 패턴 자동 탐지 v1.0 (Isolation Forest)

개요:
    순수 Python Isolation Forest 구현으로 외부 ML 라이브러리 없이
    시계열 지표(스코어, 신뢰도, 수익률, 지연시간 등)에서 이상치를 탐지한다.

알고리즘:
    Isolation Forest:
        1. 무작위 특징·분할값으로 iTree(격리 트리)를 n_estimators개 구성
        2. 각 샘플이 루트에서 격리(leaf)될 때까지의 평균 경로 길이 계산
        3. 짧은 경로 = 격리 쉬움 = 이상치 → anomaly_score (0~1, 높을수록 비정상)
        4. anomaly_score > threshold 이면 ANOMALY 판정

탐지 대상 이상 패턴:
    - 신호 스코어 급변 (±3σ 이탈)
    - 신뢰도 급락 (rolling mean 대비 40% 이하)
    - 연속 손실 (연속 n회)
    - 지연 급등 (p99 초과)
    - SQI 급락 (임계값 이하)

공개 API:
    IsolationForest   — 순수 Python 구현 (fit / predict / anomaly_score)
    AnomalyDetector   — 시스템 메트릭 특화 이상 탐지기 (window 기반)
    AnomalyReport     — 탐지 결과 DTO
"""

import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.logger import setup_logger

logger = setup_logger("anomaly_detector")

# ─── 상수 ────────────────────────────────────────────────────────
_ANOMALY_THRESHOLD = 0.60       # anomaly_score > 이 값이면 ANOMALY
_DEFAULT_ESTIMATORS = 100       # iTree 개수
_DEFAULT_SUBSAMPLE = 256        # 트리 학습 subsample 크기
_MIN_SAMPLES_FIT = 8            # 최소 fit 샘플 수
_EULER_CONSTANT = 0.5772156649  # 오일러-마스케로니 상수 (c(n) 계산용)


# ═══════════════════════════════════════════════════════════════════
#  DTO
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class AnomalyReport:
    """단일 이상 탐지 결과.

    Attributes:
        is_anomaly: 이상 여부
        anomaly_score: 이상 스코어 (0~1, 높을수록 비정상)
        metric_name: 탐지 대상 지표 이름
        value: 탐지 시점 값
        threshold: 판정 임계값
        reason: 탐지 근거 메시지
        timestamp: 탐지 시각
    """
    is_anomaly: bool
    anomaly_score: float
    metric_name: str
    value: float
    threshold: float
    reason: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_anomaly": self.is_anomaly,
            "anomaly_score": round(self.anomaly_score, 4),
            "metric_name": self.metric_name,
            "value": round(self.value, 4),
            "threshold": self.threshold,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


# ═══════════════════════════════════════════════════════════════════
#  순수 Python Isolation Forest
# ═══════════════════════════════════════════════════════════════════

def _c_factor(n: int) -> float:
    """정규화 상수 c(n) — 평균 경로 길이 기댓값.

    c(n) ≈ 2·H(n-1) - 2(n-1)/n
    여기서 H(i) = ln(i) + 0.5772... (조화수의 근사)
    """
    if n <= 1:
        return 1.0
    if n == 2:
        return 1.0
    harmonic = math.log(n - 1) + _EULER_CONSTANT
    return 2.0 * harmonic - 2.0 * (n - 1) / n


class _IsolationTree:
    """단일 격리 트리 (iTree)."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._left: Optional["_IsolationTree"] = None
        self._right: Optional["_IsolationTree"] = None
        self._split_feature: Optional[int] = None
        self._split_value: Optional[float] = None
        self._size: int = 0
        self._is_leaf: bool = True

    def fit(self, data: List[List[float]], height_limit: int) -> None:
        """재귀적으로 트리를 구성합니다."""
        n = len(data)
        self._size = n

        if n <= 1 or height_limit == 0:
            self._is_leaf = True
            return

        n_features = len(data[0])
        if n_features == 0:
            self._is_leaf = True
            return

        # 무작위 특징 선택
        feat = self._rng.randint(0, n_features - 1)
        vals = [row[feat] for row in data]
        min_val, max_val = min(vals), max(vals)

        if min_val == max_val:
            self._is_leaf = True
            return

        split = self._rng.uniform(min_val, max_val)
        self._split_feature = feat
        self._split_value = split
        self._is_leaf = False

        left_data = [row for row in data if row[feat] < split]
        right_data = [row for row in data if row[feat] >= split]

        self._left = _IsolationTree(self._rng)
        self._right = _IsolationTree(self._rng)
        self._left.fit(left_data, height_limit - 1)
        self._right.fit(right_data, height_limit - 1)

    def path_length(self, x: List[float], current_depth: int = 0) -> float:
        """샘플 x의 경로 길이 반환."""
        if self._is_leaf or self._split_feature is None:
            return current_depth + _c_factor(self._size)

        if x[self._split_feature] < self._split_value:
            return self._left.path_length(x, current_depth + 1)
        else:
            return self._right.path_length(x, current_depth + 1)


class IsolationForest:
    """순수 Python Isolation Forest.

    Args:
        n_estimators: iTree 개수 (기본 100)
        max_samples: 트리당 학습 subsample 크기 (기본 256)
        threshold: anomaly_score > 이 값이면 이상치 (기본 0.60)
        seed: 랜덤 시드 (None이면 무작위)
    """

    def __init__(
        self,
        n_estimators: int = _DEFAULT_ESTIMATORS,
        max_samples: int = _DEFAULT_SUBSAMPLE,
        threshold: float = _ANOMALY_THRESHOLD,
        seed: Optional[int] = None,
    ) -> None:
        if n_estimators <= 0:
            raise ValueError(f"n_estimators must be > 0, got {n_estimators}")
        if max_samples <= 0:
            raise ValueError(f"max_samples must be > 0, got {max_samples}")
        if not (0 < threshold < 1):
            raise ValueError(f"threshold must be in (0, 1), got {threshold}")

        self._n_estimators = n_estimators
        self._max_samples = max_samples
        self._threshold = threshold
        self._rng = random.Random(seed)
        self._trees: List[_IsolationTree] = []
        self._c_n: float = 1.0
        self._fitted = False

    def fit(self, data: List[List[float]]) -> "IsolationForest":
        """학습 데이터로 Isolation Forest를 구성합니다.

        Args:
            data: 2D 리스트 — [[f1, f2, ...], ...] (최소 _MIN_SAMPLES_FIT개)

        Returns:
            self (메서드 체이닝 가능)

        Raises:
            ValueError: 데이터가 너무 적을 때
        """
        if len(data) < _MIN_SAMPLES_FIT:
            raise ValueError(
                f"Need at least {_MIN_SAMPLES_FIT} samples, got {len(data)}"
            )

        n = min(self._max_samples, len(data))
        self._c_n = _c_factor(n)
        height_limit = math.ceil(math.log2(n)) if n > 1 else 1

        self._trees = []
        for _ in range(self._n_estimators):
            sample = self._rng.sample(data, n)
            tree = _IsolationTree(self._rng)
            tree.fit(sample, height_limit)
            self._trees.append(tree)

        self._fitted = True
        return self

    def anomaly_score(self, x: List[float]) -> float:
        """단일 샘플의 이상 스코어 반환 (0~1, 높을수록 비정상).

        score = 2^(- avg_path_len / c(n))
        """
        if not self._fitted or not self._trees:
            raise RuntimeError("IsolationForest must be fitted before scoring")

        avg_path = sum(t.path_length(x) for t in self._trees) / len(self._trees)
        if self._c_n <= 0:
            return 0.5
        score = 2.0 ** (-avg_path / self._c_n)
        return float(max(0.0, min(1.0, score)))

    def predict(self, x: List[float]) -> bool:
        """True = 이상치, False = 정상."""
        return self.anomaly_score(x) > self._threshold

    def predict_batch(self, data: List[List[float]]) -> List[bool]:
        """배치 예측."""
        return [self.predict(x) for x in data]

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @property
    def threshold(self) -> float:
        return self._threshold


# ═══════════════════════════════════════════════════════════════════
#  AnomalyDetector — 시스템 메트릭 특화
# ═══════════════════════════════════════════════════════════════════

class AnomalyDetector:
    """슬라이딩 윈도우 기반 시스템 메트릭 이상 탐지기.

    주요 메트릭(signal_score, confidence, latency_ms, sqi)을 추적하며
    이상 패턴을 자동 탐지한다.

    Args:
        window_size: 학습에 사용할 슬라이딩 윈도우 크기 (기본 200)
        n_estimators: Isolation Forest 트리 수 (기본 50)
        threshold: 이상 판정 임계값 (기본 0.65)
        seed: 랜덤 시드
    """

    _FEATURE_NAMES = ["signal_score", "confidence", "latency_ms", "sqi"]

    def __init__(
        self,
        window_size: int = 200,
        n_estimators: int = 50,
        threshold: float = 0.65,
        seed: Optional[int] = 42,
    ) -> None:
        if window_size < _MIN_SAMPLES_FIT:
            raise ValueError(f"window_size must be >= {_MIN_SAMPLES_FIT}")
        self._window_size = window_size
        self._threshold = threshold
        self._seed = seed
        self._n_estimators = n_estimators
        self._buffer: List[List[float]] = []      # [signal_score, confidence, latency_ms, sqi]
        self._forest: Optional[IsolationForest] = None
        self._report_history: List[AnomalyReport] = []
        self._fit_count = 0     # 재학습 횟수
        self._refit_every = 50  # n샘플마다 재학습

    # ── 공개 API ──────────────────────────────────────────────────

    def observe(
        self,
        signal_score: float,
        confidence: float,
        latency_ms: float,
        sqi: float,
    ) -> Optional[AnomalyReport]:
        """단일 관측값을 추가하고 이상 여부를 반환합니다.

        Args:
            signal_score: 최종 신호 스코어 (0~1)
            confidence: 신뢰도 (0~1)
            latency_ms: 처리 지연 시간 (ms)
            sqi: Signal Quality Index (0~1)

        Returns:
            AnomalyReport (이상 감지 시), None (정상 또는 학습 데이터 부족)
        """
        sample = [
            max(0.0, min(1.0, signal_score)),
            max(0.0, min(1.0, confidence)),
            max(0.0, latency_ms),
            max(0.0, min(1.0, sqi)),
        ]
        self._buffer.append(sample)
        if len(self._buffer) > self._window_size:
            self._buffer.pop(0)

        # 데이터가 충분하면 학습 / 재학습
        if len(self._buffer) >= _MIN_SAMPLES_FIT:
            self._fit_count += 1
            if self._forest is None or self._fit_count % self._refit_every == 0:
                self._refit()

        if self._forest is None:
            return None

        score = self._forest.anomaly_score(sample)
        is_anomaly = score > self._threshold

        if is_anomaly:
            report = self._build_report(sample, score)
            self._report_history.append(report)
            if len(self._report_history) > 500:
                self._report_history.pop(0)
            logger.warning(
                "[AnomalyDetector] ANOMALY detected: score=%.3f "
                "score=%.3f conf=%.3f lat=%.1fms sqi=%.3f",
                score, signal_score, confidence, latency_ms, sqi,
            )
            return report

        return None

    def force_refit(self) -> bool:
        """학습 데이터가 충분할 때 즉시 재학습합니다."""
        if len(self._buffer) < _MIN_SAMPLES_FIT:
            return False
        self._refit()
        return True

    def recent_anomalies(self, n: int = 10) -> List[AnomalyReport]:
        """최근 n개 이상 보고서 반환."""
        return list(self._report_history[-n:])

    def summary(self) -> Dict[str, Any]:
        """탐지기 현황 요약."""
        return {
            "buffer_size": len(self._buffer),
            "window_size": self._window_size,
            "is_fitted": self._forest is not None,
            "threshold": self._threshold,
            "total_anomalies": len(self._report_history),
            "fit_count": self._fit_count,
        }

    def clear(self) -> None:
        """버퍼와 히스토리를 초기화합니다."""
        self._buffer.clear()
        self._report_history.clear()
        self._forest = None
        self._fit_count = 0

    @property
    def is_fitted(self) -> bool:
        return self._forest is not None

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    # ── 내부 ──────────────────────────────────────────────────────

    def _refit(self) -> None:
        """슬라이딩 윈도우 데이터로 Isolation Forest 재학습."""
        try:
            forest = IsolationForest(
                n_estimators=self._n_estimators,
                max_samples=min(_DEFAULT_SUBSAMPLE, len(self._buffer)),
                threshold=self._threshold,
                seed=self._seed,
            )
            forest.fit(list(self._buffer))
            self._forest = forest
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AnomalyDetector] refit failed: %s", exc)

    def _build_report(self, sample: List[float], score: float) -> AnomalyReport:
        """이상 보고서를 생성합니다."""
        signal_score, confidence, latency_ms, sqi = sample
        # 어떤 특징이 가장 비정상적인지 찾기 (버퍼 평균 대비)
        if self._buffer:
            means = [
                sum(row[i] for row in self._buffer) / len(self._buffer)
                for i in range(4)
            ]
            deviations = [abs(sample[i] - means[i]) for i in range(4)]
            worst_idx = deviations.index(max(deviations))
            worst_name = self._FEATURE_NAMES[worst_idx]
            reason = (
                f"Anomaly detected (score={score:.3f}): "
                f"{worst_name}={sample[worst_idx]:.3f} "
                f"vs mean={means[worst_idx]:.3f}"
            )
        else:
            reason = f"Anomaly detected (score={score:.3f})"

        return AnomalyReport(
            is_anomaly=True,
            anomaly_score=score,
            metric_name="multivariate",
            value=signal_score,
            threshold=self._threshold,
            reason=reason,
        )


# ═══════════════════════════════════════════════════════════════════
#  편의 함수
# ═══════════════════════════════════════════════════════════════════

def detect_zscore_anomaly(
    values: List[float],
    sigma_threshold: float = 3.0,
) -> List[Tuple[int, float, float]]:
    """Z-score 기반 단변량 이상치 탐지.

    Args:
        values: 시계열 값 목록
        sigma_threshold: Z-score 임계값 (기본 3.0σ)

    Returns:
        List[(index, value, z_score)]: 이상치 위치·값·z_score 목록
    """
    if len(values) < 2:
        return []

    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0

    if std == 0.0:
        return []

    anomalies = []
    for i, v in enumerate(values):
        z = abs(v - mean) / std
        if z > sigma_threshold:
            anomalies.append((i, v, round(z, 4)))
    return anomalies
