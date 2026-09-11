"""
risk/var_calculator.py - V10 v2.1 (Session 45: mypy strict 적용)

v2.0 → v2.1 변경 사항 (mypy strict 오류 8개 해결, 실제 mypy 출력 줄 번호 기준):
    - 43번 줄: _scipy_norm = None 의 # type: ignore[assignment] 제거
      (unused-ignore. 직접 줄 대조로 확인한 결과, 43번 줄은 numpy가 아니라
       scipy 재할당 줄임. pyproject.toml에 전역 ignore_missing_imports=true가
       설정되어 있어, scipy가 미설치 상태일 경우 이미 Any로 추론되므로
       재할당에 ignore가 불필요함. numpy 줄(36번)은 numpy가 실제 설치된
       타입 스텁 패키지라 재할당 시 진짜 타입 오류가 발생하므로 ignore가
       여전히 필요 — 원본 그대로 무변경 유지)
    - 131번(RiskMetrics.kelly_meta), 133번(to_dict 반환), 192번
      (KellyCriterion.calculate 반환), 516번(VaRCalculator.calculate 반환),
      526번(calculate_kelly 반환), 566번(_get_recommendation의 kelly 파라미터):
      dict -> Dict[str, Any]
    - 519번(no-any-return): to_dict()의 반환 타입이 Dict[str, Any]로 확정되어
      return metrics.to_dict() 호출부의 Any 전파가 자동 차단됨
    - 그 외 로직/동작 100% 무변경

v2.0 (기존 유지):
    CVaR + Kelly Criterion 통합, scipy 없이 순수 Python 동작
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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
    # 🔧 Session 45: 직접 줄 대조 검증 결과 43번 줄은 이 재할당임을 확인.
    # scipy 미설치 시 ignore_missing_imports=true로 이미 Any 추론되어
    # 재할당에 type: ignore가 불필요함(unused-ignore) → 주석 제거.
    _scipy_norm = None
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
    """정규분포 역CDF (quantile function). scipy 없이 순수 Python."""
    if _HAS_SCIPY and _scipy_norm is not None:
        return float(_scipy_norm.ppf(p))
    if p <= 0.0:
        return -1e9
    if p >= 1.0:
        return 1e9
    if p > 0.5:
        sign, q = 1.0, 1.0 - p
    else:
        sign, q = -1.0, p
    t = math.sqrt(-2.0 * math.log(q))
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

    normal_var: float = 0.0
    modified_var: float = 0.0
    historical_var: float = 0.0
    cvar_95: float = 0.0
    cvar_99: float = 0.0
    skewness: float = 0.0
    kurtosis: float = 3.0
    tail_risk_adjusted: bool = False
    kelly_fraction_raw: float = 0.0
    kelly_fraction: float = 0.0
    position_limit: float = 1.0
    risk_adjustment_factor: float = 1.0
    method: str = "normal"
    recommendation: str = ""
    warning: str = ""
    data_count: int = 0
    kelly_meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
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
    """Kelly Criterion 기반 최적 포지션 크기 결정기."""

    MAX_POSITION = 0.30
    MIN_SAMPLES = 20

    def __init__(self, kelly_multiplier: float = 0.5):
        self.kelly_multiplier = max(0.1, min(1.0, kelly_multiplier))

    def calculate(
        self,
        returns: List[float],
        var_estimate: float = 0.0,
    ) -> Dict[str, Any]:
        """수익률 시계열로 Kelly fraction 계산."""
        n = len(returns)

        if n < self.MIN_SAMPLES:
            return {
                "kelly_raw": 0.0,
                "kelly_frac": 0.0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "odds_ratio": 1.0,
                "position_limit": 0.05,
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

        p = len(wins) / n
        q = 1.0 - p
        avg_win = _mean(wins)
        avg_loss = abs(_mean(losses))

        if var_estimate > 0 and var_estimate > avg_loss:
            avg_loss = var_estimate * 0.8

        if avg_loss == 0.0:
            avg_loss = 1e-6

        b = avg_win / avg_loss
        kelly_raw = (b * p - q) / b
        kelly_frac = kelly_raw * self.kelly_multiplier
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
    """Conditional VaR (Expected Shortfall) 계산기."""

    def __init__(self, confidence: float = 0.95):
        self.confidence = confidence

    def calculate_historical(self, returns: List[float]) -> float:
        if not returns:
            return 0.0
        sorted_r = sorted(returns)
        cutoff_idx = max(1, int((1 - self.confidence) * len(sorted_r)))
        tail = sorted_r[:cutoff_idx]
        return -_mean(tail) if tail else 0.0

    def calculate_gaussian(self, mu: float, sigma: float) -> float:
        if sigma <= 0:
            return max(0.0, -mu)
        alpha = 1.0 - self.confidence
        z_alpha = _norm_ppf(alpha)
        es = -mu + sigma * _norm_pdf(z_alpha) / alpha
        return max(0.0, es)

    def calculate_cornish_fisher(
        self, mu: float, sigma: float, skewness: float, kurtosis: float
    ) -> float:
        if sigma <= 0:
            return max(0.0, -mu)
        alpha = 1.0 - self.confidence
        z = _norm_ppf(alpha)
        ex_kurt = kurtosis - 3.0
        z_mod = (
            z
            + (z ** 2 - 1) * skewness / 6.0
            + (z ** 3 - 3 * z) * ex_kurt / 24.0
            - (2 * z ** 3 - 5 * z) * skewness ** 2 / 36.0
        )
        phi_z_mod = _norm_pdf(z_mod)
        cvar_cf = -mu + sigma * phi_z_mod / alpha
        return max(0.0, cvar_cf)


# ═══════════════════════════════════════════════════════════════════
#  VaRCalculator (V10 통합 인터페이스)
# ═══════════════════════════════════════════════════════════════════

class VaRCalculator:
    """V10 통합 리스크 계산기: VaR + CVaR + Kelly Criterion."""

    def __init__(
        self,
        confidence: float = 0.95,
        window: int = 252,
        kelly_multiplier: float = 0.5,
    ):
        self.confidence = confidence
        self.window = window
        self._cvar = CVaRCalculator(confidence)
        self._kelly = KellyCriterion(kelly_multiplier)

    @trace.traced
    def calculate_metrics(self, returns: List[float]) -> RiskMetrics:
        """VaR + CVaR + Kelly 통합 RiskMetrics 계산."""
        n = len(returns)

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

        cvar_95 = (
            self._cvar.calculate_cornish_fisher(mu, sigma, skewness, kurtosis)
            if tail_risk
            else self._cvar.calculate_gaussian(mu, sigma)
        )

        cvar99_calc = CVaRCalculator(0.99)
        cvar_99 = (
            cvar99_calc.calculate_cornish_fisher(mu, sigma, skewness, kurtosis)
            if tail_risk
            else cvar99_calc.calculate_gaussian(mu, sigma)
        )

        hist_cvar = self._cvar.calculate_historical(returns)
        cvar_95 = max(cvar_95, hist_cvar)

        var_pct = modified_var * 100
        if var_pct >= 5.0:
            risk_adj = 0.5
        elif var_pct >= 3.0:
            risk_adj = 0.75
        elif var_pct >= 1.5:
            risk_adj = 0.9
        else:
            risk_adj = 1.0

        kelly_result = self._kelly.calculate(returns, var_estimate=modified_var)
        position_limit = min(kelly_result["position_limit"], risk_adj)

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
    def calculate(self, returns: List[float]) -> Dict[str, Any]:
        """하위 호환 dict API (v7.x 코드와 호환 유지)."""
        metrics = self.calculate_metrics(returns)
        return metrics.to_dict()

    @trace.traced
    def calculate_kelly(
        self,
        returns: List[float],
        var: float = 0.0,
    ) -> Dict[str, Any]:
        """Kelly Criterion 독립 호출 API."""
        return self._kelly.calculate(returns, var_estimate=var)

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
        kelly: Dict[str, Any],
    ) -> str:
        parts = []
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
        cvar_pct = cvar_95 * 100
        if cvar_pct >= 7.0:
            parts.append(f"🔴 CVaR={cvar_pct:.1f}% (극단 손실 위험 높음)")
        elif cvar_pct >= 4.0:
            parts.append(f"🟡 CVaR={cvar_pct:.1f}% (손실 위험 주의)")
        if not kelly.get("valid", False):
            parts.append(f"Kelly 음수 ({kelly.get('reason', '')}): 진입 비권장")
        elif kelly["kelly_frac"] < 0.05:
            parts.append(f"Kelly 포지션 소량 ({kelly['kelly_frac']:.1%}): 탐색적 진입")
        else:
            parts.append(f"Kelly 포지션: {kelly['kelly_frac']:.1%} (승률={kelly['win_rate']:.1%})")
        return " | ".join(parts)
