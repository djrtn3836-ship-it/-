"""
risk/var_calculator.py - V10 v2.0 (CVaR + Kelly Criterion 통합)

변경 이력:
  v7.4.1  데이터 부족 시 risk_adjustment_factor: 0.7 보수적 기본값
  v2.0    V10 DDD 표준 재작성
          - CVaR (Conditional Value at Risk / Expected Shortfall) 추가
          - Kelly Criterion 포지션 크기 결정 추가
          - Fractional Kelly (Kelly × kelly_fraction) 기본 0.5 적용
          - VaR-Kelly 통합 포지션 한도 계산 (position_limit)
          - RiskMetrics dataclass 구조화 (dict → dataclass)
          - 하위 호환: calculate() 메서드 dict 반환 유지

설계 원칙:
  - scipy 없음: norm.ppf() → 순수 Python erf/erfinv 근사로 대체
  - 순수 Python 폴백 (numpy 선택적): numpy 없어도 동작
  - Kelly 적용 한도: 최대 30% (min(kelly_position, 0.30))
  - CVaR = VaR를 초과하는 손실의 기댓값 (팻테일 위험 측정)
"""

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

from observability.tracer import get_tracer

logger = logging.getLogger(__name__)
trace = get_tracer(__name__)

# ── numpy/scipy 선택적 로드 ──────────────────────────────────────────
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

try:
    from scipy.stats import norm as _scipy_norm
    _HAS_SCIPY = True
except ImportError:
    _scipy_norm = None  # type: ignore[assignment]
    _HAS_SCIPY = False


# ═══════════════════════════════════════════════════════════════════
#  Pure-Python 수학 헬퍼
# ═══════════════════════════════════════════════════════════════════

def _mean(values: List[float]) -> float:
    """산술 평균."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: List[float], mean: Optional[float] = None) -> float:
    """모표준편차 (ddof=0)."""
    if len(values) < 2:
        return 0.0
    mu = mean if mean is not None else _mean(values)
    return math.sqrt(sum((x - mu) ** 2 for x in values) / len(values))


def _norm_ppf(p: float) -> float:
    """정규분포 역CDF (quantile function). scipy 없이 순수 Python.

    Rational approximation (Abramowitz & Stegun 26.2.17 변형).
    정확도: |error| < 4.5e-4 (95th percentile 실사용 충분)
    """
    if _HAS_SCIPY and _scipy_norm is not None:
        return float(_scipy_norm.ppf(p))

    # 미러링
    if p <= 0.0:
        return -1e9
    if p >= 1.0:
        return 1e9
    if p > 0.5:
        sign, q = 1.0, 1.0 - p
    else:
        sign, q = -1.0, p

    t = math.sqrt(-2.0 * math.log(q))
    # Beasley-Springer-Moro 계수
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    result = t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)
    return sign * result


def _norm_pdf(x: float) -> float:
    """표준정규분포 PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


# ═══════════════════════════════════════════════════════════════════
#  결과 dataclass
# ═══════════════════════════════════════════════════════════════════

@dataclass
class RiskMetrics:
    """VaR/CVaR/Kelly 통합 리스크 지표."""

    # ── VaR 지표 ──────────────────────────────────────────────────
    normal_var: float = 0.0          # 정규분포 VaR
    modified_var: float = 0.0        # Cornish-Fisher 수정 VaR
    historical_var: float = 0.0      # 역사적 VaR
    cvar_95: float = 0.0             # CVaR 95% (Expected Shortfall)
    cvar_99: float = 0.0             # CVaR 99%

    # ── 분포 통계 ─────────────────────────────────────────────────
    skewness: float = 0.0
    kurtosis: float = 3.0            # 정규분포 = 3.0
    tail_risk_adjusted: bool = False

    # ── Kelly Criterion ───────────────────────────────────────────
    kelly_fraction_raw: float = 0.0  # 순수 Kelly f*
    kelly_fraction: float = 0.0      # Fractional Kelly (f* × kelly_multiplier)
    position_limit: float = 1.0      # VaR·Kelly 결합 최종 포지션 한도 (0~1)

    # ── 조정 계수 ─────────────────────────────────────────────────
    risk_adjustment_factor: float = 1.0  # 포지션 크기 스케일러
    method: str = "normal"
    recommendation: str = ""
    warning: str = ""
    data_count: int = 0

    # ── Kelly 메타 ────────────────────────────────────────────────
    kelly_meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """하위 호환용 dict 변환 (v7.x API 유지)."""
        return {
            "normal_var": self.normal_var,
            "modified_var": self.modified_var,
            "historical_var": self.historical_var,
            "cvar_95": self.cvar_95,
            "cvar_99": self.cvar_99,
            "skewness": self.skewness,
            "kurtosis": self.kurtosis,
            "tail_risk_adjusted": self.tail_risk_adjusted,
            "risk_adjustment_factor": self.risk_adjustment_factor,
            "kelly_fraction": self.kelly_fraction,
            "kelly_fraction_raw": self.kelly_fraction_raw,
            "position_limit": self.position_limit,
            "recommendation": self.recommendation,
            "method": self.method,
            "warning": self.warning,
        }


# ═══════════════════════════════════════════════════════════════════
#  Kelly Criterion 계산기
# ═══════════════════════════════════════════════════════════════════

class KellyCriterion:
    """Kelly Criterion 기반 최적 포지션 크기 결정기.

    공식:
        f* = (b × p - q) / b
        where:
            b = 평균 수익 / 평균 손실  (odds ratio)
            p = 승률 (win rate)
            q = 1 - p  (패률)

    Fractional Kelly:
        f_frac = f* × kelly_multiplier   (기본 0.5 = Half-Kelly)

    V10 한국 시장 적용:
        - 최대 포지션 한도: 30% (분산 투자 원칙)
        - 최소 데이터: 20개 이상 (통계적 유효성)
        - 손실 시그마 기반 b 추정 (VaR 활용 시)
    """

    MAX_POSITION = 0.30    # 30% 최대 포지션 한도
    MIN_SAMPLES = 20       # 통계적 유효성 최소 샘플

    def __init__(self, kelly_multiplier: float = 0.5):
        """
        Args:
            kelly_multiplier: Fractional Kelly 계수 (기본 0.5 = Half-Kelly)
                              0.5 = 안전, 1.0 = 풀 켈리 (고변동성 위험)
        """
        self.kelly_multiplier = max(0.1, min(1.0, kelly_multiplier))

    def calculate(
        self,
        returns: List[float],
        var_estimate: float = 0.0,
    ) -> dict:
        """수익률 시계열로 Kelly fraction 계산.

        Args:
            returns: 일일 수익률 리스트 (소수점, 예: 0.01 = 1%)
            var_estimate: VaR 추정값 (b 계산 보조용, 0이면 실데이터 사용)

        Returns:
            dict with keys:
                kelly_raw: 순수 Kelly f*
                kelly_frac: Fractional Kelly
                win_rate: 승률
                avg_win: 평균 수익 (양수)
                avg_loss: 평균 손실 (양수)
                odds_ratio: b = avg_win / avg_loss
                position_limit: min(kelly_frac, MAX_POSITION)
                valid: 계산 유효 여부
                reason: 무효 사유 (valid=False 시)
        """
        n = len(returns)

        if n < self.MIN_SAMPLES:
            return {
                "kelly_raw": 0.0,
                "kelly_frac": 0.0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "odds_ratio": 1.0,
                "position_limit": 0.05,   # 데이터 부족 시 보수적 5%
                "valid": False,
                "reason": f"데이터 부족 ({n}/{self.MIN_SAMPLES})",
            }

        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]

        if not wins or not losses:
            return {
                "kelly_raw": 0.0,
                "kelly_frac": 0.0,
                "win_rate": float(len(wins)) / n,
                "avg_win": _mean(wins) if wins else 0.0,
                "avg_loss": abs(_mean(losses)) if losses else 0.0,
                "odds_ratio": 1.0,
                "position_limit": 0.05,
                "valid": False,
                "reason": "승 또는 패 데이터 없음 (한쪽만 존재)",
            }

        p = len(wins) / n                    # 승률
        q = 1.0 - p                          # 패률
        avg_win = _mean(wins)                # 평균 수익
        avg_loss = abs(_mean(losses))        # 평균 손실 (양수화)

        # var_estimate로 손실 보정 (VaR가 avg_loss보다 크면 VaR 채택)
        if var_estimate > 0 and var_estimate > avg_loss:
            avg_loss = var_estimate * 0.8    # VaR의 80% (CVaR 근사)

        if avg_loss == 0.0:
            avg_loss = 1e-6                  # 0 나눗셈 방지

        b = avg_win / avg_loss               # Odds ratio

        # Kelly 공식: f* = (b*p - q) / b
        kelly_raw = (b * p - q) / b

        # Fractional Kelly
        kelly_frac = kelly_raw * self.kelly_multiplier

        # 클리핑: [0, MAX_POSITION]
        kelly_frac_clipped = max(0.0, min(kelly_frac, self.MAX_POSITION))

        return {
            "kelly_raw": kelly_raw,
            "kelly_frac": kelly_frac_clipped,
            "win_rate": p,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "odds_ratio": b,
            "position_limit": kelly_frac_clipped,
            "valid": kelly_raw > 0,
            "reason": "정상" if kelly_raw > 0 else f"음수 켈리 (f*={kelly_raw:.3f}, 기댓값 음수 구간)",
        }


# ═══════════════════════════════════════════════════════════════════
#  CVaR 계산기
# ═══════════════════════════════════════════════════════════════════

class CVaRCalculator:
    """Conditional VaR (Expected Shortfall) 계산기.

    CVaR_α = E[손실 | 손실 > VaR_α]
           = 수익률 하위 (1-α)% 구간 손실의 평균

    Cornish-Fisher 수정 CVaR (팻테일 보정):
        Modified CVaR_α = -mu + z_mod_alpha * sigma
        z_mod_alpha = -φ(Φ^{-1}(α)) / (1 - α)  (정규분포 ES)
        Cornish-Fisher 보정 항 추가

    지원 방법:
        "historical": 역사적 CVaR (비모수)
        "gaussian":   정규분포 가정 CVaR
        "cornish_fisher": Cornish-Fisher 수정 CVaR
    """

    def __init__(self, confidence: float = 0.95):
        self.confidence = confidence  # α

    def calculate_historical(self, returns: List[float]) -> float:
        """역사적 CVaR (비모수). 가장 단순하고 강건."""
        if not returns:
            return 0.0
        sorted_r = sorted(returns)
        cutoff_idx = max(1, int((1 - self.confidence) * len(sorted_r)))
        tail = sorted_r[:cutoff_idx]
        return -_mean(tail) if tail else 0.0

    def calculate_gaussian(self, mu: float, sigma: float) -> float:
        """정규분포 가정 CVaR (Expected Shortfall).

        ES_α = -μ + σ * φ(Φ^{-1}(α)) / α

        수학적 근거:
            하위 α% 손실의 기댓값 = E[-R | R < -VaR_α]
            = -μ + σ * φ(z_α) / α   (z_α = Φ^{-1}(α) < 0)
            φ(z_α) = φ(-z_α) (PDF 대칭성)으로 항상 양수

        Note:
            -(μ + σ * φ(z)/α) 공식은 부호 오류 발생 위험이 있음.
            -μ + σ * φ(z_α)/α 형태가 수치적으로 안정적.
        """
        if sigma <= 0:
            return max(0.0, -mu)
        alpha = 1.0 - self.confidence     # e.g., 0.05 for 95% confidence
        z_alpha = _norm_ppf(alpha)        # e.g., -1.645 for α=0.05
        # ES = -mu + sigma * phi(z_alpha) / alpha
        # phi(z_alpha) == phi(-z_alpha) (정규분포 PDF 대칭), 항상 양수
        es = -mu + sigma * _norm_pdf(z_alpha) / alpha
        return max(0.0, es)

    def calculate_cornish_fisher(
        self, mu: float, sigma: float, skewness: float, kurtosis: float
    ) -> float:
        """Cornish-Fisher 수정 CVaR (팻테일 반영).

        1. z_alpha = Φ^{-1}(α) → z_mod (Cornish-Fisher 보정)
        2. Modified CVaR = -mu + sigma * phi(z_mod) / alpha

        Gaussian CVaR와 동일한 부호 규칙:
            CVaR_CF = -mu + sigma * phi(z_mod) / alpha
        """
        if sigma <= 0:
            return max(0.0, -mu)
        alpha = 1.0 - self.confidence
        z = _norm_ppf(alpha)             # e.g., -1.645 for 95%

        # Cornish-Fisher 보정: z_mod (팻테일·왜도 반영)
        ex_kurt = kurtosis - 3.0
        z_mod = (
            z
            + (z ** 2 - 1) * skewness / 6.0
            + (z ** 3 - 3 * z) * ex_kurt / 24.0
            - (2 * z ** 3 - 5 * z) * skewness ** 2 / 36.0
        )

        # CVaR_CF = -mu + sigma * phi(z_mod) / alpha  (Gaussian과 동일 부호 패턴)
        phi_z_mod = _norm_pdf(z_mod)
        cvar_cf = -mu + sigma * phi_z_mod / alpha
        return max(0.0, cvar_cf)


# ═══════════════════════════════════════════════════════════════════
#  VaRCalculator (V10 통합 인터페이스)
# ═══════════════════════════════════════════════════════════════════

class VaRCalculator:
    """V10 통합 리스크 계산기: VaR + CVaR + Kelly Criterion.

    사용 예:
        calc = VaRCalculator(confidence=0.95, window=60)
        metrics = calc.calculate_metrics(returns)  # → RiskMetrics
        d = calc.calculate(returns)               # → dict (하위 호환)
        kelly = calc.calculate_kelly(returns, var=metrics.modified_var)
    """

    def __init__(
        self,
        confidence: float = 0.95,
        window: int = 252,
        kelly_multiplier: float = 0.5,
    ):
        """
        Args:
            confidence: VaR/CVaR 신뢰수준 (기본 95%)
            window: 최소 데이터 요구량 (기본 252 = 1년)
            kelly_multiplier: Fractional Kelly 계수 (기본 0.5 = Half-Kelly)
        """
        self.confidence = confidence
        self.window = window
        self._cvar = CVaRCalculator(confidence)
        self._kelly = KellyCriterion(kelly_multiplier)

    # ── 핵심 메서드 ────────────────────────────────────────────────

    @trace.traced
    def calculate_metrics(self, returns: List[float]) -> RiskMetrics:
        """VaR + CVaR + Kelly 통합 RiskMetrics 계산.

        Args:
            returns: 일일 수익률 리스트

        Returns:
            RiskMetrics dataclass
        """
        n = len(returns)

        # ── 데이터 부족 ──────────────────────────────────────────
        if n < self.window:
            kelly_result = self._kelly.calculate(returns)
            return RiskMetrics(
                risk_adjustment_factor=0.7,
                kelly_fraction_raw=kelly_result["kelly_raw"],
                kelly_fraction=kelly_result["kelly_frac"],
                position_limit=kelly_result["position_limit"],
                kelly_meta=kelly_result,
                warning=f"데이터 부족 (필요: {self.window}, 현재: {n})",
                data_count=n,
            )

        # ── 기본 통계 ──────────────────────────────────────────
        if _HAS_NUMPY:
            arr = np.array(returns, dtype=float)
            mu = float(np.mean(arr))
            sigma = float(np.std(arr))
        else:
            mu = _mean(returns)
            sigma = _std(returns, mu)

        skewness = self._calculate_skewness(returns, mu, sigma)
        kurtosis = self._calculate_kurtosis(returns, mu, sigma)
        tail_risk = kurtosis > 3.0

        # ── VaR 계산 ────────────────────────────────────────────
        z = _norm_ppf(1 - self.confidence)
        normal_var = -(mu + z * sigma)

        ex_kurt = kurtosis - 3.0
        z_mod = (
            z
            + (z ** 2 - 1) * skewness / 6.0
            + (z ** 3 - 3 * z) * ex_kurt / 24.0
            - (2 * z ** 3 - 5 * z) * skewness ** 2 / 36.0
        )
        modified_var = -(mu + z_mod * sigma)

        sorted_r = sorted(returns)
        var_idx = int((1 - self.confidence) * n)
        historical_var = -sorted_r[var_idx] if var_idx < n else 0.0

        # ── CVaR 계산 (95% / 99%) ────────────────────────────────
        cvar_95 = (
            self._cvar.calculate_cornish_fisher(mu, sigma, skewness, kurtosis)
            if tail_risk
            else self._cvar.calculate_gaussian(mu, sigma)
        )

        # 99% CVaR: 신뢰수준 일시 조정
        cvar99_calc = CVaRCalculator(0.99)
        cvar_99 = (
            cvar99_calc.calculate_cornish_fisher(mu, sigma, skewness, kurtosis)
            if tail_risk
            else cvar99_calc.calculate_gaussian(mu, sigma)
        )

        # 역사적 CVaR과 비교 → 더 보수적인 값 채택
        hist_cvar = self._cvar.calculate_historical(returns)
        cvar_95 = max(cvar_95, hist_cvar)

        # ── VaR 기반 risk_adjustment_factor ─────────────────────
        var_pct = modified_var * 100
        if var_pct >= 5.0:
            risk_adj = 0.5
        elif var_pct >= 3.0:
            risk_adj = 0.75
        elif var_pct >= 1.5:
            risk_adj = 0.9
        else:
            risk_adj = 1.0

        # ── Kelly Criterion ──────────────────────────────────────
        kelly_result = self._kelly.calculate(returns, var_estimate=modified_var)

        # VaR-Kelly 결합 포지션 한도:
        # position_limit = min(kelly_position, risk_adj)
        position_limit = min(kelly_result["position_limit"], risk_adj)

        # ── 메서드 / 권고안 ─────────────────────────────────────
        method = "cornish_fisher" if tail_risk else "normal"
        recommendation = self._get_recommendation(
            modified_var, normal_var, cvar_95, tail_risk, kelly_result
        )

        return RiskMetrics(
            normal_var=normal_var,
            modified_var=modified_var,
            historical_var=historical_var,
            cvar_95=cvar_95,
            cvar_99=cvar_99,
            skewness=skewness,
            kurtosis=kurtosis,
            tail_risk_adjusted=tail_risk,
            kelly_fraction_raw=kelly_result["kelly_raw"],
            kelly_fraction=kelly_result["kelly_frac"],
            position_limit=position_limit,
            risk_adjustment_factor=risk_adj,
            method=method,
            recommendation=recommendation,
            data_count=n,
            kelly_meta=kelly_result,
        )

    @trace.traced
    def calculate(self, returns: List[float]) -> dict:
        """하위 호환 dict API (v7.x 코드와 호환 유지)."""
        metrics = self.calculate_metrics(returns)
        return metrics.to_dict()

    @trace.traced
    def calculate_kelly(
        self,
        returns: List[float],
        var: float = 0.0,
    ) -> dict:
        """Kelly Criterion 독립 호출 API.

        Args:
            returns: 일일 수익률 리스트
            var: VaR 추정값 (b 계산 보조)

        Returns:
            KellyCriterion.calculate() 결과 dict
        """
        return self._kelly.calculate(returns, var_estimate=var)

    # ── 내부 통계 헬퍼 ────────────────────────────────────────────

    def _calculate_skewness(
        self, returns: List[float], mu: float, sigma: float
    ) -> float:
        if sigma <= 0:
            return 0.0
        if _HAS_NUMPY:
            arr = np.array(returns, dtype=float)
            return float(np.mean(((arr - mu) / sigma) ** 3))
        return _mean([(x - mu) ** 3 for x in returns]) / (sigma ** 3)

    def _calculate_kurtosis(
        self, returns: List[float], mu: float, sigma: float
    ) -> float:
        if sigma <= 0:
            return 3.0
        if _HAS_NUMPY:
            arr = np.array(returns, dtype=float)
            return float(np.mean(((arr - mu) / sigma) ** 4))
        return _mean([(x - mu) ** 4 for x in returns]) / (sigma ** 4)

    def _get_recommendation(
        self,
        modified_var: float,
        normal_var: float,
        cvar_95: float,
        tail_risk: bool,
        kelly: dict,
    ) -> str:
        parts = []

        # VaR 권고
        if not tail_risk:
            parts.append("정규분포 가정 적합 (정상 시장)")
        else:
            ratio = modified_var / normal_var if normal_var > 0 else 1.0
            if ratio > 1.3:
                parts.append("⚠️ Modified VaR 사용 권장 (팻테일 반영)")
            elif ratio > 1.1:
                parts.append("💡 Modified VaR 검토 (팻테일 가능성)")
            else:
                parts.append("✅ 정규분포 가정 유효")

        # CVaR 경고
        cvar_pct = cvar_95 * 100
        if cvar_pct >= 7.0:
            parts.append(f"🔴 CVaR={cvar_pct:.1f}% (극단 손실 위험 높음)")
        elif cvar_pct >= 4.0:
            parts.append(f"🟡 CVaR={cvar_pct:.1f}% (손실 위험 주의)")

        # Kelly 권고
        if not kelly.get("valid", False):
            parts.append(f"Kelly 음수 ({kelly.get('reason', '')}): 진입 비권장")
        elif kelly["kelly_frac"] < 0.05:
            parts.append(f"Kelly 포지션 소량 ({kelly['kelly_frac']:.1%}): 탐색적 진입")
        else:
            parts.append(f"Kelly 포지션: {kelly['kelly_frac']:.1%} (승률={kelly['win_rate']:.1%})")

        return " | ".join(parts)
