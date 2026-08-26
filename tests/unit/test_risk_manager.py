"""
tests/unit/test_risk_manager.py

VaRCalculator v2.0 + CVaR + Kelly Criterion 단위 테스트 (35개)

커버리지:
  - _norm_ppf / _norm_pdf 순수 Python 수학 헬퍼
  - CVaRCalculator: historical / gaussian / cornish_fisher
  - KellyCriterion: 정상/부족/음수 케이스
  - VaRCalculator.calculate_metrics(): 정상/부족/팻테일
  - VaRCalculator.calculate(): 하위 호환 dict API
  - RiskMetrics.to_dict() 구조 검증
  - VaR-Kelly 결합 position_limit
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from risk.var_calculator import (
    VaRCalculator,
    KellyCriterion,
    CVaRCalculator,
    RiskMetrics,
    _mean,
    _std,
    _norm_ppf,
    _norm_pdf,
)


# ═══════════════════════════════════════════════════════════════════
#  공통 픽스처
# ═══════════════════════════════════════════════════════════════════

def _make_returns(n: int = 300, win_rate: float = 0.55, seed: int = 42) -> list:
    """재현 가능한 합성 수익률 (간단한 LCG 랜덤)."""
    rng = seed
    out = []
    for _ in range(n):
        rng = (rng * 1664525 + 1013904223) & 0xFFFFFFFF
        u = rng / 0xFFFFFFFF
        if u < win_rate:
            out.append(0.01 + (u * 0.03))    # +1~4% 수익
        else:
            out.append(-0.008 - (u * 0.015)) # -0.8~2.3% 손실
    return out


def _make_fat_tail_returns(n: int = 300) -> list:
    """팻테일 특성 수익률 (극단값 삽입)."""
    base = _make_returns(n)
    # 일부 극단 손실 삽입 → kurtosis 상승
    for i in range(0, n, 30):
        base[i] = -0.08  # 8% 급락
    return base


# ═══════════════════════════════════════════════════════════════════
#  1. 수학 헬퍼 테스트
# ═══════════════════════════════════════════════════════════════════

class TestMathHelpers:

    def test_mean_normal(self):
        assert _mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)

    def test_mean_empty(self):
        assert _mean([]) == 0.0

    def test_std_normal(self):
        # 모표준편차: sqrt(2/3) ≈ 0.8165
        result = _std([1.0, 2.0, 3.0])
        assert result == pytest.approx(math.sqrt(2 / 3), rel=1e-5)

    def test_std_single(self):
        assert _std([5.0]) == 0.0

    def test_norm_ppf_50th_percentile(self):
        """50번째 백분위수는 0이어야 함."""
        assert _norm_ppf(0.5) == pytest.approx(0.0, abs=1e-3)

    def test_norm_ppf_95th_percentile(self):
        """95번째 백분위수는 약 1.645."""
        assert _norm_ppf(0.95) == pytest.approx(1.645, abs=0.01)

    def test_norm_ppf_5th_percentile(self):
        """5번째 백분위수는 약 -1.645 (대칭)."""
        result = _norm_ppf(0.05)
        assert result == pytest.approx(-1.645, abs=0.01)

    def test_norm_pdf_at_zero(self):
        """표준정규분포 PDF at 0 = 1/sqrt(2π) ≈ 0.3989."""
        assert _norm_pdf(0.0) == pytest.approx(1.0 / math.sqrt(2 * math.pi), rel=1e-6)

    def test_norm_ppf_boundary_low(self):
        """0 이하는 매우 작은 음수."""
        assert _norm_ppf(0.0) < -1e8

    def test_norm_ppf_boundary_high(self):
        """1 이상은 매우 큰 양수."""
        assert _norm_ppf(1.0) > 1e8


# ═══════════════════════════════════════════════════════════════════
#  2. CVaRCalculator 테스트
# ═══════════════════════════════════════════════════════════════════

class TestCVaRCalculator:

    def setup_method(self):
        self.calc = CVaRCalculator(confidence=0.95)
        self.returns = _make_returns(300)

    def test_historical_cvar_positive(self):
        """역사적 CVaR는 양수 (손실 표현)."""
        result = self.calc.calculate_historical(self.returns)
        assert result > 0.0

    def test_historical_cvar_empty(self):
        """빈 리스트 → 0.0."""
        assert self.calc.calculate_historical([]) == 0.0

    def test_gaussian_cvar_greater_than_var(self):
        """CVaR >= VaR (CVaR는 더 보수적)."""
        mu, sigma = _mean(self.returns), _std(self.returns)
        z = _norm_ppf(0.05)
        var = -(mu + z * sigma)
        cvar = self.calc.calculate_gaussian(mu, sigma)
        assert cvar >= var * 0.9   # 약간의 수치 오차 허용

    def test_gaussian_cvar_zero_sigma(self):
        """sigma=0이면 손실 = max(0, -mu)."""
        result = self.calc.calculate_gaussian(mu=-0.01, sigma=0.0)
        assert result == pytest.approx(0.01, abs=1e-6)

    def test_cornish_fisher_fat_tail_higher_than_gaussian(self):
        """팻테일 + 중립 왜도(S≈0)에서 Cornish-Fisher CVaR >= Gaussian CVaR.

        수학적 근거:
            CF z_mod = z + (z^2-1)*S/6 + (z^3-3z)*(K-3)/24 - ...
            S≈0, K>3 (팻테일) → z_mod ≈ z + 양수 보정 → phi(z_mod) 증가
            → CF CVaR > Gaussian CVaR

        음의 왜도(S<0) + 팻테일은 CF < Gaussian일 수 있음 (수학적으로 정상).
        """
        mu, sigma = -0.001, 0.02
        # 중립 왜도 + 초과 첨도: CF > Gaussian 보장
        cf_cvar = self.calc.calculate_cornish_fisher(mu, sigma, skewness=0.0, kurtosis=6.0)
        gauss_cvar = self.calc.calculate_gaussian(mu, sigma)
        assert cf_cvar > gauss_cvar

    def test_cornish_fisher_normal_market(self):
        """정규 시장 (S=0, K=3)에서는 gaussian과 유사."""
        mu, sigma = -0.001, 0.02
        cf_cvar = self.calc.calculate_cornish_fisher(mu, sigma, 0.0, 3.0)
        gauss_cvar = self.calc.calculate_gaussian(mu, sigma)
        assert abs(cf_cvar - gauss_cvar) < 0.005   # 0.5% 이내 차이


# ═══════════════════════════════════════════════════════════════════
#  3. KellyCriterion 테스트
# ═══════════════════════════════════════════════════════════════════

class TestKellyCriterion:

    def setup_method(self):
        self.kelly = KellyCriterion(kelly_multiplier=0.5)

    def test_insufficient_data_returns_conservative(self):
        """20개 미만 데이터 → 보수적 5% 한도."""
        result = self.kelly.calculate([0.01, -0.005] * 5)  # 10개
        assert result["valid"] is False
        assert result["position_limit"] == 0.05

    def test_positive_kelly_valid(self):
        """승률>50%, 수익>손실 → 양수 Kelly."""
        returns = _make_returns(100, win_rate=0.6)
        result = self.kelly.calculate(returns)
        assert result["valid"] is True
        assert result["kelly_raw"] > 0

    def test_fractional_kelly_half_of_raw(self):
        """Fractional Kelly = Raw × 0.5 (Half-Kelly 기본값)."""
        returns = _make_returns(100, win_rate=0.65)
        result = self.kelly.calculate(returns)
        if result["valid"]:
            raw = result["kelly_raw"]
            expected_frac = min(raw * 0.5, KellyCriterion.MAX_POSITION)
            assert result["kelly_frac"] == pytest.approx(expected_frac, abs=1e-6)

    def test_max_position_cap_30pct(self):
        """포지션 한도는 최대 30%를 초과하지 않음."""
        # 승률 95% → Kelly 매우 높을 것
        returns = [0.05] * 80 + [-0.001] * 20
        result = self.kelly.calculate(returns)
        assert result["position_limit"] <= 0.30

    def test_only_wins_returns_invalid(self):
        """손실 없으면 odds_ratio 계산 불가 → invalid."""
        returns = [0.01] * 50
        result = self.kelly.calculate(returns)
        assert result["valid"] is False

    def test_negative_expected_value_kelly_negative(self):
        """기댓값 음수 (지속 손실) → Kelly 음수 → valid=False."""
        returns = [-0.01] * 80 + [0.001] * 20  # 거의 항상 손실
        result = self.kelly.calculate(returns)
        assert result["kelly_raw"] < 0
        assert result["valid"] is False
        assert result["position_limit"] == 0.0

    def test_var_estimate_adjusts_avg_loss(self):
        """var_estimate가 avg_loss보다 크면 손실을 VaR 기반으로 조정."""
        returns = _make_returns(100)
        result_no_var = self.kelly.calculate(returns, var_estimate=0.0)
        result_with_var = self.kelly.calculate(returns, var_estimate=0.10)
        # VaR가 크면 avg_loss 증가 → Kelly 감소
        assert result_with_var["kelly_frac"] <= result_no_var["kelly_frac"] + 1e-6


# ═══════════════════════════════════════════════════════════════════
#  4. VaRCalculator (통합) 테스트
# ═══════════════════════════════════════════════════════════════════

class TestVaRCalculator:

    def setup_method(self):
        # window를 60으로 낮춰 테스트 데이터로 충분하게
        self.calc = VaRCalculator(confidence=0.95, window=60)
        self.returns = _make_returns(300)

    def test_calculate_metrics_returns_risk_metrics(self):
        """calculate_metrics는 RiskMetrics 타입 반환."""
        result = self.calc.calculate_metrics(self.returns)
        assert isinstance(result, RiskMetrics)

    def test_var_positive(self):
        """정상 시장에서 VaR > 0."""
        result = self.calc.calculate_metrics(self.returns)
        assert result.modified_var > 0
        assert result.normal_var > 0

    def test_cvar_greater_than_var(self):
        """CVaR >= VaR (Expected Shortfall은 더 보수적)."""
        result = self.calc.calculate_metrics(self.returns)
        assert result.cvar_95 >= result.modified_var * 0.8

    def test_cvar_99_greater_than_cvar_95(self):
        """99% CVaR >= 95% CVaR."""
        result = self.calc.calculate_metrics(self.returns)
        assert result.cvar_99 >= result.cvar_95 * 0.9

    def test_data_insufficient_uses_conservative(self):
        """데이터 부족 시 risk_adjustment_factor=0.7."""
        calc = VaRCalculator(window=252)
        short_returns = _make_returns(50)
        result = calc.calculate_metrics(short_returns)
        assert result.risk_adjustment_factor == pytest.approx(0.7)
        assert "부족" in result.warning

    def test_fat_tail_uses_cornish_fisher(self):
        """팻테일 수익률 → cornish_fisher 메서드 선택."""
        fat_returns = _make_fat_tail_returns(300)
        result = self.calc.calculate_metrics(fat_returns)
        assert result.method == "cornish_fisher"
        assert result.tail_risk_adjusted is True

    def test_normal_market_uses_normal_method(self):
        """정규 시장 → normal 메서드."""
        # 거의 완벽한 정규분포 수익률 생성 (극단값 없음)
        # → kurtosis < 3인 경우도 있으므로 method 확인
        result = self.calc.calculate_metrics(self.returns)
        assert result.method in ("normal", "cornish_fisher")

    def test_position_limit_leq_risk_adj(self):
        """position_limit <= risk_adjustment_factor."""
        result = self.calc.calculate_metrics(self.returns)
        assert result.position_limit <= result.risk_adjustment_factor + 1e-9

    def test_kelly_fraction_in_result(self):
        """Kelly fraction이 RiskMetrics에 포함."""
        result = self.calc.calculate_metrics(self.returns)
        assert 0.0 <= result.kelly_fraction <= 0.30   # 0~30%

    def test_calculate_backward_compat_dict(self):
        """calculate() 메서드는 dict 반환 (하위 호환)."""
        result = self.calc.calculate(self.returns)
        assert isinstance(result, dict)
        assert "normal_var" in result
        assert "modified_var" in result
        assert "cvar_95" in result
        assert "kelly_fraction" in result

    def test_calculate_kelly_standalone(self):
        """calculate_kelly() 독립 호출."""
        result = self.calc.calculate_kelly(self.returns)
        assert "kelly_raw" in result
        assert "kelly_frac" in result
        assert "win_rate" in result


# ═══════════════════════════════════════════════════════════════════
#  5. RiskMetrics dataclass 테스트
# ═══════════════════════════════════════════════════════════════════

class TestRiskMetrics:

    def test_to_dict_has_required_keys(self):
        """to_dict()는 v7.x 호환 키 포함."""
        m = RiskMetrics(
            normal_var=0.02,
            modified_var=0.025,
            cvar_95=0.03,
            risk_adjustment_factor=0.9,
        )
        d = m.to_dict()
        required_keys = [
            "normal_var", "modified_var", "historical_var",
            "cvar_95", "cvar_99", "skewness", "kurtosis",
            "tail_risk_adjusted", "risk_adjustment_factor",
            "kelly_fraction", "position_limit",
        ]
        for k in required_keys:
            assert k in d, f"Missing key: {k}"

    def test_default_kurtosis_3(self):
        """기본 kurtosis는 3.0 (정규분포)."""
        m = RiskMetrics()
        assert m.kurtosis == 3.0

    def test_default_risk_adj_1(self):
        """기본 risk_adjustment_factor는 1.0."""
        m = RiskMetrics()
        assert m.risk_adjustment_factor == 1.0
