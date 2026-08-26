"""
risk/portfolio_var.py - v2.0 (VaRCalculator Kelly Criterion 통합)

v1.0: Monte Carlo 기반 포트폴리오 VaR / CVaR
v2.0 변경사항:
    - PortfolioRiskMetrics에 kelly_position_limit 필드 추가
    - PortfolioVaR에 VaRCalculator 의존성 주입 (선택적)
    - calculate()에서 포트폴리오 합산 수익률로 Kelly fraction 계산
    - 최종 position_limit = min(var_risk_adj_factor, kelly_position_limit)
    - _fallback_individual_var()도 Kelly 통합 적용
    - 하위 호환 완전 유지 (기존 필드 변경 없음)

통합 로직:
    portfolio_returns = Σ (weight_i × returns_i)  ← 포트폴리오 합산 수익률
    kelly = VaRCalculator.calculate_kelly(portfolio_returns)
    position_limit = min(risk_adj_factor, kelly["position_limit"])
"""

import logging
from dataclasses import dataclass, field
from typing import List

import numpy as np

from observability.tracer import get_tracer

logger = logging.getLogger(__name__)
trace = get_tracer(__name__)


@dataclass
class PortfolioRiskMetrics:
    """포트폴리오 리스크 지표 (v2.0 Kelly 통합)"""

    var_95: float                # 95% VaR (단일일 손실)
    var_99: float                # 99% VaR
    cvar_95: float               # 95% CVaR (Expected Shortfall)
    std_dev: float               # 포트폴리오 표준편차
    expected_return: float       # 기대 수익률
    risk_adj_factor: float       # VaR 기반 글로벌 리스크 조정 계수 (0.5~1.0)
    simulation_count: int        # 실제 시뮬레이션 횟수
    status: str                  # 'OK', 'DATA_INSUFFICIENT', 'SINGLE_ASSET'
    # v2.0 추가 필드 (Kelly 통합)
    kelly_position_limit: float = 1.0    # Kelly Criterion 포지션 한도 (0~0.30)
    position_limit: float = 1.0          # 최종 한도 = min(risk_adj_factor, kelly_position_limit)
    kelly_win_rate: float = 0.0          # 포트폴리오 수익률 기반 승률
    kelly_valid: bool = False            # Kelly 계산 유효 여부
    kelly_meta: dict = field(default_factory=dict)  # Kelly 상세 메타데이터


def _calc_portfolio_returns(
    tickers: List[str],
    returns_dict: dict,
    weights: dict,
) -> List[float]:
    """종목별 수익률 × 가중치 합산 → 포트폴리오 일별 수익률.

    Args:
        tickers: 종목 코드 리스트
        returns_dict: {ticker: [일별 수익률]}
        weights: 정규화된 {ticker: 비중}

    Returns:
        포트폴리오 일별 합산 수익률 (float 리스트)
    """
    # 사용 가능한 종목 및 최소 길이 파악
    valid_tickers = [t for t in tickers if t in returns_dict and returns_dict[t]]
    if not valid_tickers:
        return []

    min_len = min(len(returns_dict[t]) for t in valid_tickers)
    if min_len == 0:
        return []

    portfolio_returns: List[float] = []
    for i in range(min_len):
        day_ret = sum(
            returns_dict[t][-min_len:][i] * weights.get(t, 0.0)
            for t in valid_tickers
        )
        portfolio_returns.append(day_ret)
    return portfolio_returns


def _calc_risk_adj_factor(var_pct: float) -> float:
    """VaR % 기준 리스크 조정 계수 산출.

    구간:
        var ≥ 5.0%  → 0.50 (매우 고위험)
        var ≥ 3.0%  → 0.75 (고위험)
        var ≥ 1.5%  → 0.90 (중위험)
        var < 1.5%  → 1.00 (저위험)
    """
    if var_pct >= 5.0:
        return 0.50
    elif var_pct >= 3.0:
        return 0.75
    elif var_pct >= 1.5:
        return 0.90
    return 1.00


class PortfolioVaR:
    """포트폴리오 VaR 계산기 (Monte Carlo 기반, v2.0 Kelly 통합)"""

    def __init__(
        self,
        confidence: float = 0.95,
        num_simulations: int = 10_000,
        lookback_days: int = 252,
        var_calculator=None,       # VaRCalculator 인스턴스 (선택적 주입)
    ):
        """
        Args:
            confidence: 신뢰수준 (기본 95%)
            num_simulations: Monte Carlo 시뮬레이션 횟수
            lookback_days: 수익률 계산을 위한 과거 데이터 일수
            var_calculator: VaRCalculator 인스턴스. None이면 내부에서 생성.
                            순환 임포트 방지를 위해 lazy 주입 지원.
        """
        self.confidence = confidence
        self.num_simulations = num_simulations
        self.lookback_days = lookback_days
        self._var_calculator = var_calculator  # 지연 주입 가능

    def _get_var_calculator(self):
        """VaRCalculator lazy getter — 순환 임포트 방지."""
        if self._var_calculator is None:
            from risk.var_calculator import VaRCalculator  # noqa: PLC0415
            self._var_calculator = VaRCalculator(confidence=self.confidence)
        return self._var_calculator

    def _compute_kelly(self, portfolio_returns: List[float], var_estimate: float = 0.0) -> dict:
        """포트폴리오 합산 수익률로 Kelly fraction 계산.

        Args:
            portfolio_returns: 포트폴리오 일별 수익률
            var_estimate: 포트폴리오 VaR (Kelly b 보정용)

        Returns:
            KellyCriterion.calculate() 결과 dict
        """
        try:
            vc = self._get_var_calculator()
            return vc.calculate_kelly(portfolio_returns, var=var_estimate)
        except Exception as exc:
            logger.warning("⚠️ Portfolio Kelly 계산 실패 (비치명): %s", exc)
            return {
                "kelly_raw": 0.0,
                "kelly_frac": 0.0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "odds_ratio": 1.0,
                "position_limit": 1.0,
                "valid": False,
                "reason": f"Kelly 계산 예외: {exc}",
            }

    @trace.traced
    def calculate(
        self,
        tickers: List[str],
        returns_dict: dict,
        weights: dict,
    ) -> PortfolioRiskMetrics:
        """포트폴리오 VaR + Kelly 통합 계산.

        Args:
            tickers: 종목 코드 리스트
            returns_dict: {ticker: [일일 수익률 리스트]} (길이 일치해야 함)
            weights: {ticker: 포트폴리오 비중} (합계 1.0)

        Returns:
            PortfolioRiskMetrics (v2.0: kelly_position_limit, position_limit 포함)
        """
        # ── 1. 입력 검증 ─────────────────────────────────────────────
        if not tickers:
            return PortfolioRiskMetrics(
                var_95=0.0, var_99=0.0, cvar_95=0.0, std_dev=0.0,
                expected_return=0.0, risk_adj_factor=1.0,
                simulation_count=0, status="NO_ASSETS",
            )

        # ── 2. 가중치 정규화 ──────────────────────────────────────────
        total_weight = sum(weights.values())
        if total_weight == 0:
            return PortfolioRiskMetrics(
                var_95=0.0, var_99=0.0, cvar_95=0.0, std_dev=0.0,
                expected_return=0.0, risk_adj_factor=1.0,
                simulation_count=0, status="NO_WEIGHT",
            )
        normalized_weights = {t: w / total_weight for t, w in weights.items()}

        # ── 3. 수익률 데이터 정렬 ─────────────────────────────────────
        min_len = min(
            len(returns_dict.get(t, [])) for t in tickers if t in returns_dict
        )
        if min_len < 30:
            logger.warning(
                "⚠️ 포트폴리오 VaR: 데이터 부족 (최소 %d일, 30일 필요) → 개별 VaR 합산", min_len
            )
            return self._fallback_individual_var(returns_dict, normalized_weights)

        aligned_returns = []
        for t in tickers:
            ret = returns_dict.get(t, [])
            if len(ret) > min_len:
                ret = ret[-min_len:]
            aligned_returns.append(ret)

        returns_matrix = np.array(aligned_returns).T  # (days, n_assets)

        # ── 4. 기본 통계 ──────────────────────────────────────────────
        mean_returns = np.mean(returns_matrix, axis=0)
        cov_matrix = np.cov(returns_matrix, rowvar=False)
        weight_array = np.array([normalized_weights.get(t, 0.0) for t in tickers])

        portfolio_mean = float(np.dot(weight_array, mean_returns))
        portfolio_var_val = float(np.dot(weight_array.T, np.dot(cov_matrix, weight_array)))
        portfolio_std = float(np.sqrt(portfolio_var_val)) if portfolio_var_val > 0 else 0.0

        # ── 5. Monte Carlo 시뮬레이션 ─────────────────────────────────
        if len(tickers) == 1:
            simulated_returns = np.random.normal(mean_returns[0], portfolio_std, self.num_simulations)
        else:
            try:
                L = np.linalg.cholesky(cov_matrix + np.eye(len(tickers)) * 1e-8)
                Z = np.random.normal(0, 1, (self.num_simulations, len(tickers)))
                correlated_returns = np.dot(Z, L.T) + mean_returns
                simulated_returns = np.dot(correlated_returns, weight_array)
            except np.linalg.LinAlgError:
                logger.warning("⚠️ Cholesky 분해 실패, 의사역행렬로 대체")
                try:
                    pseudo_cov = np.linalg.pinv(cov_matrix + np.eye(len(tickers)) * 1e-8)
                    L = np.linalg.cholesky(pseudo_cov + np.eye(len(tickers)) * 1e-8)
                    Z = np.random.normal(0, 1, (self.num_simulations, len(tickers)))
                    correlated_returns = np.dot(Z, L.T) + mean_returns
                    simulated_returns = np.dot(correlated_returns, weight_array)
                except Exception:
                    logger.warning("⚠️ 상관관계 행렬 처리 실패, 독립 가정으로 전환")
                    simulated_returns = np.random.normal(
                        portfolio_mean, portfolio_std, self.num_simulations
                    )

        # ── 6. VaR / CVaR 계산 ───────────────────────────────────────
        sorted_returns = np.sort(simulated_returns)
        var_95_idx = int(self.confidence * self.num_simulations)
        var_99_idx = int(0.99 * self.num_simulations)

        var_95 = float(-sorted_returns[var_95_idx]) if var_95_idx < len(sorted_returns) else 0.0
        var_99 = float(-sorted_returns[var_99_idx]) if var_99_idx < len(sorted_returns) else 0.0

        tail_returns = sorted_returns[:var_95_idx]
        cvar_95 = float(-np.mean(tail_returns)) if len(tail_returns) > 0 else var_95

        # ── 7. VaR 기반 risk_adj_factor ──────────────────────────────
        risk_adj = _calc_risk_adj_factor(var_95 * 100)

        # ── 8. Kelly Criterion (v2.0 신규) ───────────────────────────
        portfolio_returns = _calc_portfolio_returns(tickers, returns_dict, normalized_weights)
        kelly_result = self._compute_kelly(portfolio_returns, var_estimate=var_95)
        kelly_pos_limit = kelly_result["position_limit"]

        # 최종 포지션 한도: VaR 조정 계수 × Kelly 한도 중 보수적인 쪽
        final_position_limit = min(risk_adj, kelly_pos_limit)

        logger.debug(
            "📊 포트폴리오 VaR=%.2f%%, risk_adj=%.2f, kelly_limit=%.2f → position_limit=%.2f",
            var_95 * 100, risk_adj, kelly_pos_limit, final_position_limit,
        )

        return PortfolioRiskMetrics(
            var_95=var_95,
            var_99=var_99,
            cvar_95=cvar_95,
            std_dev=portfolio_std,
            expected_return=portfolio_mean,
            risk_adj_factor=risk_adj,
            simulation_count=self.num_simulations,
            status="OK",
            kelly_position_limit=kelly_pos_limit,
            position_limit=final_position_limit,
            kelly_win_rate=kelly_result.get("win_rate", 0.0),
            kelly_valid=kelly_result.get("valid", False),
            kelly_meta=kelly_result,
        )

    @trace.traced
    def _fallback_individual_var(
        self, returns_dict: dict, weights: dict
    ) -> PortfolioRiskMetrics:
        """데이터 부족 시 개별 VaR의 가중 합산으로 포트폴리오 VaR 추정 (v2.0 Kelly 포함)."""
        total_var = 0.0
        total_cvar = 0.0
        total_std = 0.0
        total_return = 0.0
        valid_count = 0
        all_returns: List[float] = []

        for ticker, weight in weights.items():
            returns = returns_dict.get(ticker, [])
            if len(returns) < 5:
                continue
            sorted_ret = np.sort(returns)
            idx = int(0.95 * len(sorted_ret))
            var_i = float(-sorted_ret[idx]) if idx < len(sorted_ret) else 0.0
            cvar_i = float(-np.mean(sorted_ret[:idx])) if idx > 0 else var_i
            std_i = float(np.std(returns))

            total_var += var_i * weight
            total_cvar += cvar_i * weight
            total_std += std_i * weight
            total_return += float(np.mean(returns)) * weight
            all_returns.extend([r * weight for r in returns])   # 가중 합산 수익률 수집
            valid_count += 1

        if valid_count == 0:
            return PortfolioRiskMetrics(
                var_95=0.0, var_99=0.0, cvar_95=0.0, std_dev=0.0,
                expected_return=0.0, risk_adj_factor=1.0,
                simulation_count=0, status="DATA_INSUFFICIENT",
            )

        risk_adj = _calc_risk_adj_factor(total_var * 100)

        # Kelly 계산 (가중 합산 수익률 사용)
        kelly_result = self._compute_kelly(all_returns, var_estimate=total_var)
        kelly_pos_limit = kelly_result["position_limit"]
        final_position_limit = min(risk_adj, kelly_pos_limit)

        return PortfolioRiskMetrics(
            var_95=total_var,
            var_99=total_var * 1.2,      # 99% VaR 대략적 추정
            cvar_95=total_cvar,
            std_dev=total_std,
            expected_return=total_return,
            risk_adj_factor=risk_adj,
            simulation_count=0,
            status="DATA_INSUFFICIENT",
            kelly_position_limit=kelly_pos_limit,
            position_limit=final_position_limit,
            kelly_win_rate=kelly_result.get("win_rate", 0.0),
            kelly_valid=kelly_result.get("valid", False),
            kelly_meta=kelly_result,
        )
