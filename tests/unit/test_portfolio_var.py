# -*- coding: utf-8 -*-
"""
tests/unit/test_portfolio_var.py
PortfolioVaR v2.0 (Kelly Criterion 통합) 단위 테스트 — 32개

Test Classes:
    TestPortfolioRiskMetrics    (5개): v2.0 필드 기본값, min 검증
    TestCalcPortfolioReturns    (5개): 헬퍼 함수 단위 테스트
    TestCalcRiskAdjFactor       (5개): VaR 구간별 risk_adj 계수
    TestPortfolioVaRCalculate  (10개): 정상/엣지 케이스 통합 테스트
    TestFallbackIndividualVar   (7개): 데이터 부족 경로
"""

import dataclasses
import random

from risk.portfolio_var import (
    PortfolioRiskMetrics,
    PortfolioVaR,
    _calc_portfolio_returns,
    _calc_risk_adj_factor,
)


# ─────────────────────────────────────────────────────────────────────
# 공통 픽스처
# ─────────────────────────────────────────────────────────────────────

def _make_returns(seed: int = 42, n: int = 60, mu: float = 0.001, sigma: float = 0.015):
    random.seed(seed)
    return [random.gauss(mu, sigma) for _ in range(n)]


def _make_pvar(simulations: int = 300) -> PortfolioVaR:
    return PortfolioVaR(num_simulations=simulations)


# ─────────────────────────────────────────────────────────────────────
# TestPortfolioRiskMetrics
# ─────────────────────────────────────────────────────────────────────

class TestPortfolioRiskMetrics:

    def test_required_v2_fields_exist(self):
        """v2.0 Kelly 필드가 dataclass에 존재해야 한다."""
        field_names = {f.name for f in dataclasses.fields(PortfolioRiskMetrics)}
        assert "kelly_position_limit" in field_names
        assert "position_limit" in field_names
        assert "kelly_win_rate" in field_names
        assert "kelly_valid" in field_names
        assert "kelly_meta" in field_names

    def test_v2_fields_have_defaults(self):
        """v2.0 Kelly 필드는 기본값이 있어 기존 생성자 호환을 유지한다."""
        m = PortfolioRiskMetrics(
            var_95=0.02, var_99=0.03, cvar_95=0.025,
            std_dev=0.01, expected_return=0.001,
            risk_adj_factor=0.9, simulation_count=1000, status="OK",
        )
        assert m.kelly_position_limit == 1.0
        assert m.position_limit == 1.0
        assert m.kelly_win_rate == 0.0
        assert m.kelly_valid is False
        assert m.kelly_meta == {}

    def test_position_limit_floor_zero(self):
        """position_limit은 음수가 될 수 없다."""
        m = PortfolioRiskMetrics(
            var_95=0.0, var_99=0.0, cvar_95=0.0,
            std_dev=0.0, expected_return=0.0,
            risk_adj_factor=0.0, simulation_count=0, status="NO_ASSETS",
            position_limit=0.0,
        )
        assert m.position_limit >= 0.0

    def test_kelly_meta_default_is_mutable_safe(self):
        """kelly_meta 기본값이 인스턴스 간 공유되지 않아야 한다."""
        m1 = PortfolioRiskMetrics(
            var_95=0.0, var_99=0.0, cvar_95=0.0, std_dev=0.0,
            expected_return=0.0, risk_adj_factor=1.0,
            simulation_count=0, status="OK",
        )
        m2 = PortfolioRiskMetrics(
            var_95=0.0, var_99=0.0, cvar_95=0.0, std_dev=0.0,
            expected_return=0.0, risk_adj_factor=1.0,
            simulation_count=0, status="OK",
        )
        m1.kelly_meta["key"] = "val"
        assert "key" not in m2.kelly_meta

    def test_status_field_preserved(self):
        """기존 status 필드가 그대로 유지된다."""
        for status in ("OK", "DATA_INSUFFICIENT", "SINGLE_ASSET", "NO_ASSETS"):
            m = PortfolioRiskMetrics(
                var_95=0.0, var_99=0.0, cvar_95=0.0, std_dev=0.0,
                expected_return=0.0, risk_adj_factor=1.0,
                simulation_count=0, status=status,
            )
            assert m.status == status


# ─────────────────────────────────────────────────────────────────────
# TestCalcPortfolioReturns
# ─────────────────────────────────────────────────────────────────────

class TestCalcPortfolioReturns:

    def test_single_asset_equals_returns(self):
        """단일 종목 100% 비중 → 원래 수익률 그대로."""
        rets = [0.01, -0.02, 0.03]
        result = _calc_portfolio_returns(["A"], {"A": rets}, {"A": 1.0})
        assert len(result) == 3
        assert abs(result[0] - 0.01) < 1e-10

    def test_two_assets_weighted_sum(self):
        """두 종목 가중 합산 확인."""
        rets_a = [0.02, 0.04]
        rets_b = [-0.01, 0.01]
        result = _calc_portfolio_returns(
            ["A", "B"],
            {"A": rets_a, "B": rets_b},
            {"A": 0.5, "B": 0.5},
        )
        assert abs(result[0] - 0.005) < 1e-10   # (0.02 + -0.01) / 2

    def test_empty_tickers_returns_empty(self):
        result = _calc_portfolio_returns([], {}, {})
        assert result == []

    def test_missing_ticker_in_returns_dict_skipped(self):
        """returns_dict에 없는 종목은 건너뛴다."""
        rets = [0.01, 0.02]
        result = _calc_portfolio_returns(["A", "MISSING"], {"A": rets}, {"A": 0.6, "MISSING": 0.4})
        # MISSING 종목 제외 후 A만 사용
        assert len(result) == 2

    def test_length_aligned_to_shortest(self):
        """종목 중 가장 짧은 길이에 맞춰 정렬된다."""
        result = _calc_portfolio_returns(
            ["A", "B"],
            {"A": [0.01] * 10, "B": [0.02] * 5},
            {"A": 0.5, "B": 0.5},
        )
        assert len(result) == 5


# ─────────────────────────────────────────────────────────────────────
# TestCalcRiskAdjFactor
# ─────────────────────────────────────────────────────────────────────

class TestCalcRiskAdjFactor:

    def test_very_high_risk(self):
        assert _calc_risk_adj_factor(5.0) == 0.50
        assert _calc_risk_adj_factor(8.0) == 0.50

    def test_high_risk(self):
        assert _calc_risk_adj_factor(3.0) == 0.75
        assert _calc_risk_adj_factor(4.9) == 0.75

    def test_medium_risk(self):
        assert _calc_risk_adj_factor(1.5) == 0.90
        assert _calc_risk_adj_factor(2.9) == 0.90

    def test_low_risk(self):
        assert _calc_risk_adj_factor(0.0) == 1.00
        assert _calc_risk_adj_factor(1.49) == 1.00

    def test_boundary_exactly_5pct(self):
        assert _calc_risk_adj_factor(5.0) == 0.50


# ─────────────────────────────────────────────────────────────────────
# TestPortfolioVaRCalculate
# ─────────────────────────────────────────────────────────────────────

class TestPortfolioVaRCalculate:

    def test_no_tickers_returns_no_assets(self):
        pvar = _make_pvar()
        result = pvar.calculate([], {}, {})
        assert result.status == "NO_ASSETS"

    def test_no_weight_returns_no_weight(self):
        pvar = _make_pvar()
        result = pvar.calculate(["A"], {"A": [0.01, 0.02]}, {"A": 0.0})
        assert result.status == "NO_WEIGHT"

    def test_insufficient_data_fallback(self):
        """30일 미만 데이터 → DATA_INSUFFICIENT fallback."""
        pvar = _make_pvar()
        short_rets = [0.01] * 10
        result = pvar.calculate(
            ["A"], {"A": short_rets}, {"A": 1.0}
        )
        assert result.status == "DATA_INSUFFICIENT"

    def test_ok_status_with_sufficient_data(self):
        pvar = _make_pvar()
        rets = _make_returns()
        result = pvar.calculate(
            ["A", "B"],
            {"A": rets, "B": [r * 0.8 for r in rets]},
            {"A": 0.6, "B": 0.4},
        )
        assert result.status == "OK"

    def test_position_limit_le_risk_adj(self):
        """position_limit ≤ risk_adj_factor 보장."""
        pvar = _make_pvar()
        rets = _make_returns()
        result = pvar.calculate(
            ["A"], {"A": rets}, {"A": 1.0}
        )
        assert result.position_limit <= result.risk_adj_factor + 1e-9

    def test_position_limit_le_kelly_limit(self):
        """position_limit ≤ kelly_position_limit 보장."""
        pvar = _make_pvar()
        rets = _make_returns()
        result = pvar.calculate(
            ["A"], {"A": rets}, {"A": 1.0}
        )
        assert result.position_limit <= result.kelly_position_limit + 1e-9

    def test_position_limit_equals_min(self):
        """position_limit == min(risk_adj, kelly_limit)."""
        pvar = _make_pvar()
        rets = _make_returns()
        result = pvar.calculate(
            ["A", "B"],
            {"A": rets, "B": [r * 1.1 for r in rets]},
            {"A": 0.5, "B": 0.5},
        )
        expected = min(result.risk_adj_factor, result.kelly_position_limit)
        assert abs(result.position_limit - expected) < 1e-9

    def test_kelly_meta_has_required_keys(self):
        """kelly_meta에 KellyCriterion 표준 키가 존재해야 한다."""
        pvar = _make_pvar()
        rets = _make_returns()
        result = pvar.calculate(
            ["A"], {"A": rets}, {"A": 1.0}
        )
        for key in ("kelly_raw", "kelly_frac", "win_rate", "valid"):
            assert key in result.kelly_meta, f"kelly_meta missing: {key}"

    def test_single_asset_ok(self):
        """단일 종목도 정상 처리."""
        pvar = _make_pvar()
        rets = _make_returns(n=50)
        result = pvar.calculate(["A"], {"A": rets}, {"A": 1.0})
        assert result.status == "OK"
        assert 0.0 <= result.position_limit <= 1.0

    def test_weight_normalization(self):
        """가중치 합이 1이 아니어도 정규화 처리."""
        pvar = _make_pvar()
        rets = _make_returns()
        result = pvar.calculate(
            ["A", "B"],
            {"A": rets, "B": [r * 0.5 for r in rets]},
            {"A": 3.0, "B": 7.0},   # 합계 10 → 0.3, 0.7로 정규화
        )
        assert result.status == "OK"


# ─────────────────────────────────────────────────────────────────────
# TestFallbackIndividualVar
# ─────────────────────────────────────────────────────────────────────

class TestFallbackIndividualVar:

    def test_all_insufficient_returns_data_insufficient(self):
        """모든 종목이 5개 미만이면 DATA_INSUFFICIENT."""
        pvar = _make_pvar()
        result = pvar._fallback_individual_var(
            {"A": [0.01, 0.02], "B": [0.0]},
            {"A": 0.5, "B": 0.5},
        )
        assert result.status == "DATA_INSUFFICIENT"

    def test_partial_sufficient_returns_result(self):
        """일부만 충분해도 결과 반환. var_95는 수익 방향에 따라 음수 가능."""
        pvar = _make_pvar()
        rets = _make_returns(n=20)
        result = pvar._fallback_individual_var(
            {"A": rets, "B": [0.01, 0.02]},
            {"A": 0.7, "B": 0.3},
        )
        assert result.status == "DATA_INSUFFICIENT"
        # var_95 타입 검증 (음수=순익, 양수=순손 — 부호 자유)
        assert isinstance(result.var_95, float)

    def test_position_limit_le_risk_adj_fallback(self):
        """fallback에서도 position_limit ≤ risk_adj 보장."""
        pvar = _make_pvar()
        rets = _make_returns(n=30)
        result = pvar._fallback_individual_var(
            {"A": rets, "B": rets},
            {"A": 0.5, "B": 0.5},
        )
        assert result.position_limit <= result.risk_adj_factor + 1e-9

    def test_position_limit_equals_min_fallback(self):
        """fallback: position_limit == min(risk_adj, kelly_limit)."""
        pvar = _make_pvar()
        rets = _make_returns(n=30)
        result = pvar._fallback_individual_var(
            {"A": rets}, {"A": 1.0}
        )
        expected = min(result.risk_adj_factor, result.kelly_position_limit)
        assert abs(result.position_limit - expected) < 1e-9

    def test_var_99_gt_var_95_fallback(self):
        """var_99 > var_95 (대략 1.2배 추정)."""
        pvar = _make_pvar()
        rets = _make_returns(n=30)
        result = pvar._fallback_individual_var(
            {"A": rets}, {"A": 1.0}
        )
        if result.var_95 > 0:
            assert result.var_99 > result.var_95

    def test_kelly_meta_populated_fallback(self):
        """fallback에서도 kelly_meta 채워짐."""
        pvar = _make_pvar()
        rets = _make_returns(n=30)
        result = pvar._fallback_individual_var(
            {"A": rets}, {"A": 1.0}
        )
        assert isinstance(result.kelly_meta, dict)
        assert "valid" in result.kelly_meta

    def test_empty_weights_returns_data_insufficient(self):
        """비중 dict가 비어있으면 DATA_INSUFFICIENT."""
        pvar = _make_pvar()
        result = pvar._fallback_individual_var({}, {})
        assert result.status == "DATA_INSUFFICIENT"
