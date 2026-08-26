# -*- coding: utf-8 -*-
"""
application/analysis/ab_framework.py - A/B Testing Framework v1.0 (Phase 3)

설계 원칙:
    - 순수 Python (scipy/statsmodels 불필요) — Welch t-test 자체 구현
    - 싱글톤 ABTestManager: 전역 실험 레지스트리
    - 스레드-세이프 결과 누적 (asyncio.Lock)
    - 통계 검정: Welch's t-test + p-value < 0.05 유의성 판단
    - Bonferroni 보정: 다중 비교 시 α/n 분할
    - 보상 클리핑 [-100%, +100%] 이상치 방지

주요 클래스:
    ABVariant       — 개별 변형 (이름, 비중, 결과 누적)
    ABTest          — 실험 단위 (이름, 변형들, 기간, 통계 API)
    ABTestManager   — 전역 레지스트리 (싱글톤)

통계 메서드:
    _welch_t_stat(a, b) → (t, df)    — Welch t-통계량 + 자유도
    _t_cdf_approx(t, df) → p         — t분포 CDF 근사 (Abramowitz & Stegun)
    _p_value_two_tail(t, df) → p      — 양측 p-value

수명 주기:
    manager = get_ab_manager()
    test = manager.create_test("price_model", ["control", "variant_a"], traffic_split=[0.5, 0.5])
    variant = manager.assign_variant("price_model", user_id="u123")
    manager.record_result("price_model", variant, metric_value=0.03)
    winner = manager.get_winner("price_model")
"""

import asyncio
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from observability.tracer import get_tracer

logger_std = __import__("logging").getLogger(__name__)
trace = get_tracer(__name__)

# ──────────────────────────────────────────────────────────────────────────────
#  수학 헬퍼 — Welch t-test 순수 Python 구현
# ──────────────────────────────────────────────────────────────────────────────

_CLIP_MIN = -1.0
_CLIP_MAX = +1.0
_MIN_SIGNIFICANCE_LEVEL = 0.05     # 기본 유의수준 α
_MIN_SAMPLES_FOR_TEST = 10         # 통계 검정 최소 샘플


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _var(values: List[float], ddof: int = 1) -> float:
    if len(values) <= ddof:
        return 0.0
    m = _mean(values)
    return sum((x - m) ** 2 for x in values) / (len(values) - ddof)


def _welch_t_stat(a: List[float], b: List[float]) -> Tuple[float, float]:
    """Welch's t-통계량과 자유도(df) 반환.

    t = (μ_a - μ_b) / sqrt(s_a²/n_a + s_b²/n_b)
    df = (s_a²/n_a + s_b²/n_b)² / (
             (s_a²/n_a)²/(n_a-1) + (s_b²/n_b)²/(n_b-1)
         )  — Welch-Satterthwaite

    Returns:
        (t_stat, degrees_of_freedom)
    """
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return 0.0, 1.0

    va = _var(a) / na   # s_a² / n_a
    vb = _var(b) / nb

    denom = math.sqrt(va + vb)
    if denom < 1e-12:
        return 0.0, float(na + nb - 2)

    t = (_mean(a) - _mean(b)) / denom

    # Welch-Satterthwaite 자유도
    numerator = (va + vb) ** 2
    denominator = (va ** 2) / max(na - 1, 1) + (vb ** 2) / max(nb - 1, 1)
    df = numerator / denominator if denominator > 1e-12 else float(na + nb - 2)

    return t, max(df, 1.0)


def _regularized_incomplete_beta(x: float, a: float, b: float, max_iter: int = 200) -> float:
    """정규화 불완전 베타 함수 I_x(a, b) — 연분수 근사 (Lentz 알고리즘).

    t-분포 CDF 계산의 기초.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    # 수렴 가속: x > (a+1)/(a+b+2) 이면 대칭 관계 사용
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _regularized_incomplete_beta(1.0 - x, b, a, max_iter)

    # 로그 스케일 전처리
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(a * math.log(x) + b * math.log(1.0 - x) - lbeta) / a

    # Lentz 연분수
    f = 1.0
    C = f
    D = 0.0
    eps = 1e-12

    for m in range(max_iter):
        for step in (0, 1):
            if m == 0 and step == 0:
                d = 1.0
            elif step == 0:
                d = m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
            else:
                d = -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))

            D = 1.0 + d * D
            if abs(D) < eps:
                D = eps
            D = 1.0 / D

            C = 1.0 + d / C
            if abs(C) < eps:
                C = eps

            f *= C * D
            if abs(C * D - 1.0) < 1e-10:
                break

    return front * (f - 1.0)


def _t_cdf(t: float, df: float) -> float:
    """t-분포 누적 분포 함수 P(T ≤ t).

    I_x(a, b) 관계: P = I_{df/(df+t²)}(df/2, 1/2) / 2  (t < 0)
    """
    x = df / (df + t * t)
    p_half = _regularized_incomplete_beta(x, df / 2.0, 0.5) / 2.0
    if t >= 0:
        return 1.0 - p_half
    return p_half


def _p_value_two_tail(t: float, df: float) -> float:
    """양측 p-value = 2 × P(T > |t|)."""
    return 2.0 * (1.0 - _t_cdf(abs(t), df))


# ──────────────────────────────────────────────────────────────────────────────
#  도메인 모델
# ──────────────────────────────────────────────────────────────────────────────

class TestStatus(Enum):
    RUNNING = "running"
    CONCLUDED = "concluded"
    STOPPED = "stopped"


@dataclass
class ABVariant:
    """A/B 테스트 개별 변형.

    Attributes:
        name: 변형 식별자 (e.g., "control", "variant_a")
        traffic_weight: 트래픽 비중 (0~1, 총합 = 1.0)
        results: 누적 결과 리스트 (클리핑 적용된 metric 값)
        assignment_count: 이 변형에 배정된 총 사용자 수
    """
    name: str
    traffic_weight: float = 0.5
    results: List[float] = field(default_factory=list)
    assignment_count: int = 0

    @property
    def n(self) -> int:
        return len(self.results)

    @property
    def mean(self) -> float:
        return _mean(self.results)

    @property
    def std(self) -> float:
        v = _var(self.results)
        return math.sqrt(v) if v > 0 else 0.0

    def record(self, value: float) -> None:
        """결과 기록 (클리핑 적용)."""
        clipped = max(_CLIP_MIN, min(_CLIP_MAX, value))
        self.results.append(clipped)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "traffic_weight": self.traffic_weight,
            "n": self.n,
            "mean": round(self.mean, 6),
            "std": round(self.std, 6),
            "assignment_count": self.assignment_count,
        }


@dataclass
class StatResult:
    """통계 검정 결과."""
    tested: bool = False                  # 검정 시행 여부 (샘플 충분 여부)
    winner: Optional[str] = None          # 승리 변형 이름 (유의미한 차이 있을 때)
    p_value: float = 1.0                  # 양측 p-value
    t_stat: float = 0.0                   # Welch t-통계량
    degrees_of_freedom: float = 1.0       # 자유도
    significant: bool = False             # p < alpha
    alpha: float = _MIN_SIGNIFICANCE_LEVEL
    reason: str = "미검정"                # 결과 설명
    effect_size: float = 0.0              # Cohen's d

    def to_dict(self) -> dict:
        return {
            "tested": self.tested,
            "winner": self.winner,
            "p_value": round(self.p_value, 6),
            "t_stat": round(self.t_stat, 4),
            "degrees_of_freedom": round(self.degrees_of_freedom, 2),
            "significant": self.significant,
            "alpha": self.alpha,
            "reason": self.reason,
            "effect_size": round(self.effect_size, 4),
        }


class ABTest:
    """개별 A/B 테스트 실험.

    수명 주기: RUNNING → (CONCLUDED | STOPPED)
    """

    def __init__(
        self,
        name: str,
        variants: List[ABVariant],
        alpha: float = _MIN_SIGNIFICANCE_LEVEL,
        min_samples: int = _MIN_SAMPLES_FOR_TEST,
    ):
        self.name = name
        self.variants: Dict[str, ABVariant] = {v.name: v for v in variants}
        self.alpha = alpha
        self.min_samples = min_samples
        self.status = TestStatus.RUNNING
        self.start_time: float = time.time()
        self.end_time: Optional[float] = None
        self.test_id: str = str(uuid.uuid4())[:8]
        self._lock = asyncio.Lock()

    # ── 변형 배정 ──────────────────────────────────────────────────────────

    def assign(self, user_id: str) -> str:
        """user_id 해시 기반 결정론적 변형 배정.

        동일 user_id는 항상 동일 변형에 배정됩니다 (재현성).

        Args:
            user_id: 사용자 식별자 (str, hash 가능)

        Returns:
            변형 이름
        """
        # 변형 목록을 이름 순으로 정렬하여 항상 동일한 순서 보장
        sorted_variants = sorted(self.variants.values(), key=lambda v: v.name)
        total = sum(v.traffic_weight for v in sorted_variants)
        if total <= 0:
            return sorted_variants[0].name

        # 해시 기반 버킷 (0~1 범위)
        bucket = (hash(f"{self.name}:{user_id}") % 10_000) / 10_000.0

        cumulative = 0.0
        for variant in sorted_variants:
            cumulative += variant.traffic_weight / total
            if bucket < cumulative:
                variant.assignment_count += 1
                return variant.name

        # 마지막 변형으로 fallback (부동소수점 오차)
        last = sorted_variants[-1]
        last.assignment_count += 1
        return last.name

    # ── 결과 기록 ──────────────────────────────────────────────────────────

    async def record(self, variant_name: str, value: float) -> None:
        """비동기 결과 기록 (스레드-세이프).

        Args:
            variant_name: 변형 이름
            value: 측정 메트릭 값 (수익률 등)
        """
        async with self._lock:
            if variant_name not in self.variants:
                logger_std.warning(
                    "ABTest[%s]: 알 수 없는 변형 '%s' — 무시", self.name, variant_name
                )
                return
            if self.status != TestStatus.RUNNING:
                logger_std.debug(
                    "ABTest[%s]: 종료된 테스트에 결과 기록 시도 — 무시", self.name
                )
                return
            self.variants[variant_name].record(value)

    # ── 통계 분석 ──────────────────────────────────────────────────────────

    def analyze(self, bonferroni: bool = True) -> StatResult:
        """Welch t-test로 변형 간 유의성 검정.

        현재 구현: 2-변형 비교 (control vs 나머지 최고 평균)
        다중 변형 시 Bonferroni 보정 적용.

        Args:
            bonferroni: True이면 Bonferroni α 보정 적용

        Returns:
            StatResult
        """
        variants = list(self.variants.values())
        if len(variants) < 2:
            return StatResult(reason="변형이 2개 미만")

        # 최소 샘플 확인
        if any(v.n < self.min_samples for v in variants):
            min_n = min(v.n for v in variants)
            return StatResult(
                reason=f"샘플 부족 (최소 {min_n}/{self.min_samples})"
            )

        # Bonferroni 보정: 다중 비교 수 = C(k, 2)
        k = len(variants)
        n_comparisons = max(1, k * (k - 1) // 2)
        alpha_adj = self.alpha / n_comparisons if bonferroni and n_comparisons > 1 else self.alpha

        # 최고 평균 vs 나머지 비교 (best pair)
        best_pair: Optional[Tuple[ABVariant, ABVariant, float, float, float]] = None
        max_t_abs = -1.0

        for i in range(len(variants)):
            for j in range(i + 1, len(variants)):
                a, b = variants[i], variants[j]
                t, df = _welch_t_stat(a.results, b.results)
                p = _p_value_two_tail(t, df)
                if abs(t) > max_t_abs:
                    max_t_abs = abs(t)
                    best_pair = (a, b, t, df, p)

        if best_pair is None:
            return StatResult(reason="비교 불가")

        a, b, t, df, p = best_pair
        significant = p < alpha_adj

        # 승리 변형 (유의미한 차이가 있는 경우만)
        winner: Optional[str] = None
        if significant:
            winner = a.name if a.mean > b.mean else b.name

        # Cohen's d 효과 크기
        pooled_std = math.sqrt((_var(a.results) + _var(b.results)) / 2.0)
        effect_size = abs(a.mean - b.mean) / pooled_std if pooled_std > 1e-12 else 0.0

        reason = (
            f"유의미한 차이: {winner} 승 (p={p:.4f} < α={alpha_adj:.4f})"
            if significant
            else f"유의미한 차이 없음 (p={p:.4f} ≥ α={alpha_adj:.4f})"
        )

        return StatResult(
            tested=True,
            winner=winner,
            p_value=p,
            t_stat=t,
            degrees_of_freedom=df,
            significant=significant,
            alpha=alpha_adj,
            reason=reason,
            effect_size=effect_size,
        )

    # ── 상태 관리 ──────────────────────────────────────────────────────────

    def conclude(self) -> StatResult:
        """실험 종료 및 최종 통계 반환."""
        self.status = TestStatus.CONCLUDED
        self.end_time = time.time()
        return self.analyze()

    def stop(self) -> None:
        """실험 강제 중지 (결과 없음)."""
        self.status = TestStatus.STOPPED
        self.end_time = time.time()

    # ── 상태 조회 ──────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        elapsed = time.time() - self.start_time
        stat = self.analyze()
        return {
            "test_id": self.test_id,
            "name": self.name,
            "status": self.status.value,
            "elapsed_sec": round(elapsed, 1),
            "variants": {k: v.to_dict() for k, v in self.variants.items()},
            "statistics": stat.to_dict(),
        }


# ──────────────────────────────────────────────────────────────────────────────
#  ABTestManager — 전역 레지스트리 (싱글톤)
# ──────────────────────────────────────────────────────────────────────────────

class ABTestManager:
    """A/B 테스트 전역 레지스트리 (싱글톤).

    Usage::

        manager = get_ab_manager()
        manager.create_test(
            "strategy_comparison",
            variant_names=["control", "ml_enhanced"],
            traffic_split=[0.5, 0.5],
        )
        variant = manager.assign_variant("strategy_comparison", user_id="AAPL")
        await manager.record_result("strategy_comparison", variant, 0.025)
        winner = manager.get_winner("strategy_comparison")
    """

    _instance: Optional["ABTestManager"] = None

    def __new__(cls) -> "ABTestManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._tests: Dict[str, ABTest] = {}
        self._lock = asyncio.Lock()
        logger_std.info("✅ ABTestManager 초기화 완료")

    # ── 테스트 생성 ────────────────────────────────────────────────────────

    @trace.traced
    def create_test(
        self,
        test_name: str,
        variant_names: List[str],
        traffic_split: Optional[List[float]] = None,
        alpha: float = _MIN_SIGNIFICANCE_LEVEL,
        min_samples: int = _MIN_SAMPLES_FOR_TEST,
        overwrite: bool = False,
    ) -> ABTest:
        """새 A/B 테스트 생성.

        Args:
            test_name: 실험 이름 (중복 방지)
            variant_names: 변형 이름 목록 (최소 2개)
            traffic_split: 각 변형의 트래픽 비중 (None이면 균등 분할)
            alpha: 유의수준 (기본 0.05)
            min_samples: 통계 검정 최소 샘플 수
            overwrite: True이면 기존 동일 이름 실험 덮어쓰기

        Returns:
            ABTest 인스턴스

        Raises:
            ValueError: variant_names < 2 또는 traffic_split 길이 불일치
        """
        if len(variant_names) < 2:
            raise ValueError(f"변형이 최소 2개 필요합니다. 제공: {len(variant_names)}")

        if traffic_split is None:
            split = [1.0 / len(variant_names)] * len(variant_names)
        else:
            if len(traffic_split) != len(variant_names):
                raise ValueError(
                    f"traffic_split 길이({len(traffic_split)})가 "
                    f"variant_names 길이({len(variant_names)})와 다릅니다."
                )
            split = traffic_split

        if test_name in self._tests and not overwrite:
            logger_std.warning("ABTestManager: '%s' 이미 존재 — overwrite=False", test_name)
            return self._tests[test_name]

        variants = [
            ABVariant(name=n, traffic_weight=w)
            for n, w in zip(variant_names, split)
        ]
        test = ABTest(name=test_name, variants=variants, alpha=alpha, min_samples=min_samples)
        self._tests[test_name] = test
        logger_std.info(
            "✅ ABTest 생성: '%s' 변형=%s, α=%.3f, min_samples=%d",
            test_name, variant_names, alpha, min_samples,
        )
        return test

    # ── 변형 배정 ──────────────────────────────────────────────────────────

    @trace.traced
    def assign_variant(self, test_name: str, user_id: str) -> Optional[str]:
        """user_id에 변형 배정.

        Args:
            test_name: 실험 이름
            user_id: 사용자/종목 식별자

        Returns:
            변형 이름 (실험 없으면 None)
        """
        test = self._tests.get(test_name)
        if test is None:
            logger_std.debug("ABTestManager: '%s' 실험 없음", test_name)
            return None
        if test.status != TestStatus.RUNNING:
            logger_std.debug("ABTestManager: '%s' 실험이 실행 중이 아님", test_name)
            return None
        return test.assign(user_id)

    # ── 결과 기록 ──────────────────────────────────────────────────────────

    async def record_result(
        self, test_name: str, variant_name: str, metric_value: float
    ) -> bool:
        """비동기 결과 기록.

        Args:
            test_name: 실험 이름
            variant_name: 변형 이름
            metric_value: 메트릭 값 (수익률 등)

        Returns:
            기록 성공 여부
        """
        test = self._tests.get(test_name)
        if test is None:
            return False
        await test.record(variant_name, metric_value)
        return True

    # ── 분석 / 승리자 결정 ─────────────────────────────────────────────────

    @trace.traced
    def get_winner(self, test_name: str, bonferroni: bool = True) -> Optional[str]:
        """현재 통계 검정 기준 승리 변형 이름 반환.

        Args:
            test_name: 실험 이름
            bonferroni: Bonferroni 보정 적용 여부

        Returns:
            승리 변형 이름 또는 None (유의미한 차이 없음)
        """
        test = self._tests.get(test_name)
        if test is None:
            return None
        return test.analyze(bonferroni=bonferroni).winner

    @trace.traced
    def get_stats(self, test_name: str) -> Optional[dict]:
        """실험 전체 상태 + 통계 반환.

        Args:
            test_name: 실험 이름

        Returns:
            get_status() dict 또는 None
        """
        test = self._tests.get(test_name)
        return test.get_status() if test else None

    @trace.traced
    def conclude_test(self, test_name: str) -> Optional[StatResult]:
        """실험 종료 + 최종 결과 반환.

        Args:
            test_name: 실험 이름

        Returns:
            StatResult 또는 None
        """
        test = self._tests.get(test_name)
        if test is None:
            return None
        return test.conclude()

    def list_tests(self) -> Dict[str, str]:
        """전체 실험 목록 {name: status}."""
        return {name: t.status.value for name, t in self._tests.items()}

    def get_all_status(self) -> List[dict]:
        """전체 실험 상태 리스트."""
        return [t.get_status() for t in self._tests.values()]


# ──────────────────────────────────────────────────────────────────────────────
#  전역 싱글톤 접근자
# ──────────────────────────────────────────────────────────────────────────────

_ab_manager: Optional[ABTestManager] = None


def get_ab_manager() -> ABTestManager:
    """ABTestManager 전역 싱글톤 반환."""
    global _ab_manager
    if _ab_manager is None:
        _ab_manager = ABTestManager()
    return _ab_manager
