"""
risk/portfolio_var.py - v1.0 FINAL (포트폴리오 VaR + Monte Carlo 시뮬레이션)
- 포트폴리오 전체의 VaR(Value at Risk) 및 CVaR(Expected Shortfall) 계산
- Monte Carlo 시뮬레이션(10,000회)으로 극단적 손실 분포 추정
- 상관관계 행렬 기반 Cholesky 분해로 종목 간 의존성 반영
- 데이터 부족 시 개별 VaR 합산으로 Fallback
"""

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PortfolioRiskMetrics:
    """포트폴리오 리스크 지표"""

    var_95: float  # 95% VaR (단일일 손실)
    var_99: float  # 99% VaR
    cvar_95: float  # 95% CVaR (Expected Shortfall)
    std_dev: float  # 포트폴리오 표준편차
    expected_return: float  # 기대 수익률
    risk_adj_factor: float  # 글로벌 리스크 조정 계수 (0.5~1.0)
    simulation_count: int  # 실제 시뮬레이션 횟수
    status: str  # 'OK', 'DATA_INSUFFICIENT', 'SINGLE_ASSET'


class PortfolioVaR:
    """포트폴리오 VaR 계산기 (Monte Carlo 기반)"""

    def __init__(self, confidence: float = 0.95, num_simulations: int = 10000, lookback_days: int = 252):
        """
        Args:
            confidence: 신뢰수준 (기본 95%)
            num_simulations: Monte Carlo 시뮬레이션 횟수
            lookback_days: 수익률 계산을 위한 과거 데이터 일수
        """
        self.confidence = confidence
        self.num_simulations = num_simulations
        self.lookback_days = lookback_days

    def calculate(
        self, tickers: list[str], returns_dict: dict[str, list[float]], weights: dict[str, float]
    ) -> PortfolioRiskMetrics:
        """
        포트폴리오 VaR 계산

        Args:
            tickers: 종목 코드 리스트
            returns_dict: {ticker: [일일 수익률 리스트]} (길이 일치해야 함)
            weights: {ticker: 포트폴리오 비중} (합계 1.0)
        Returns:
            PortfolioRiskMetrics
        """
        # 1. 입력 검증
        if not tickers or len(tickers) == 0:
            return PortfolioRiskMetrics(
                var_95=0.0,
                var_99=0.0,
                cvar_95=0.0,
                std_dev=0.0,
                expected_return=0.0,
                risk_adj_factor=1.0,
                simulation_count=0,
                status="NO_ASSETS",
            )

        # 2. 가중치 정규화
        total_weight = sum(weights.values())
        if total_weight == 0:
            return PortfolioRiskMetrics(
                var_95=0.0,
                var_99=0.0,
                cvar_95=0.0,
                std_dev=0.0,
                expected_return=0.0,
                risk_adj_factor=1.0,
                simulation_count=0,
                status="NO_WEIGHT",
            )
        normalized_weights = {t: w / total_weight for t, w in weights.items()}

        # 3. 수익률 데이터 정렬 (모든 종목의 데이터 길이를 동일하게 맞춤)
        min_len = min(len(returns_dict.get(t, [])) for t in tickers if t in returns_dict)
        if min_len < 30:
            # 데이터 부족 → 개별 VaR 합산으로 Fallback
            logger.warning(f"⚠️ 포트폴리오 VaR: 데이터 부족 (최소 {min_len}일, 30일 필요) → 개별 VaR 합산")
            return self._fallback_individual_var(returns_dict, normalized_weights)

        # 동일한 길이로 자르기 (최신 데이터 기준)
        aligned_returns = []
        for t in tickers:
            ret = returns_dict.get(t, [])
            if len(ret) > min_len:
                ret = ret[-min_len:]
            aligned_returns.append(ret)

        returns_matrix = np.array(aligned_returns).T  # shape: (days, n_assets)

        # 4. 기본 통계
        mean_returns = np.mean(returns_matrix, axis=0)
        cov_matrix = np.cov(returns_matrix, rowvar=False)
        weight_array = np.array([normalized_weights.get(t, 0.0) for t in tickers])

        # 포트폴리오 기대 수익률 및 분산
        portfolio_mean = np.dot(weight_array, mean_returns)
        portfolio_var = np.dot(weight_array.T, np.dot(cov_matrix, weight_array))
        portfolio_std = np.sqrt(portfolio_var) if portfolio_var > 0 else 0.0

        # 5. Monte Carlo 시뮬레이션 (Cholesky 분해로 상관관계 반영)
        if len(tickers) == 1:
            # 단일 종목: 정규분포 가정
            simulated_returns = np.random.normal(mean_returns[0], portfolio_std, self.num_simulations)
        else:
            # 다중 종목: Cholesky 분해
            try:
                L = np.linalg.cholesky(cov_matrix + np.eye(len(tickers)) * 1e-8)
                Z = np.random.normal(0, 1, (self.num_simulations, len(tickers)))
                correlated_returns = np.dot(Z, L.T) + mean_returns
                simulated_returns = np.dot(correlated_returns, weight_array)
            except np.linalg.LinAlgError:
                # Cholesky 실패 시 (비정칙 행렬) → 의사역행렬 사용
                logger.warning("⚠️ Cholesky 분해 실패, 의사역행렬로 대체")
                try:
                    pseudo_cov = np.linalg.pinv(cov_matrix + np.eye(len(tickers)) * 1e-8)
                    L = np.linalg.cholesky(pseudo_cov + np.eye(len(tickers)) * 1e-8)
                    Z = np.random.normal(0, 1, (self.num_simulations, len(tickers)))
                    correlated_returns = np.dot(Z, L.T) + mean_returns
                    simulated_returns = np.dot(correlated_returns, weight_array)
                except:
                    # 최종 Fallback: 독립 가정
                    logger.warning("⚠️ 상관관계 행렬 처리 실패, 독립 가정으로 전환")
                    simulated_returns = np.random.normal(portfolio_mean, portfolio_std, self.num_simulations)

        # 6. VaR 및 CVaR 계산
        sorted_returns = np.sort(simulated_returns)
        var_95_idx = int(self.confidence * self.num_simulations)
        var_99_idx = int(0.99 * self.num_simulations)

        var_95 = -sorted_returns[var_95_idx] if var_95_idx < len(sorted_returns) else 0.0
        var_99 = -sorted_returns[var_99_idx] if var_99_idx < len(sorted_returns) else 0.0

        # CVaR (Expected Shortfall): VaR를 초과하는 손실의 평균
        tail_returns = sorted_returns[:var_95_idx]
        cvar_95 = -np.mean(tail_returns) if len(tail_returns) > 0 else var_95

        # 7. 글로벌 리스크 조정 계수 (포트폴리오 레벨)
        # var_95가 5% 이상이면 0.5, 3% 이상이면 0.75, 1.5% 이상이면 0.9
        var_pct = var_95 * 100
        if var_pct >= 5.0:
            risk_adj = 0.5
        elif var_pct >= 3.0:
            risk_adj = 0.75
        elif var_pct >= 1.5:
            risk_adj = 0.9
        else:
            risk_adj = 1.0

        return PortfolioRiskMetrics(
            var_95=var_95,
            var_99=var_99,
            cvar_95=cvar_95,
            std_dev=portfolio_std,
            expected_return=portfolio_mean,
            risk_adj_factor=risk_adj,
            simulation_count=self.num_simulations,
            status="OK",
        )

    def _fallback_individual_var(
        self, returns_dict: dict[str, list[float]], weights: dict[str, float]
    ) -> PortfolioRiskMetrics:
        """데이터 부족 시 개별 VaR의 가중 합산으로 포트폴리오 VaR 추정"""
        total_var = 0.0
        total_cvar = 0.0
        total_std = 0.0
        total_return = 0.0
        valid_count = 0

        for ticker, weight in weights.items():
            returns = returns_dict.get(ticker, [])
            if len(returns) < 5:
                continue
            # 간단한 Historical VaR (95%)
            sorted_ret = np.sort(returns)
            idx = int(0.95 * len(sorted_ret))
            var_i = -sorted_ret[idx] if idx < len(sorted_ret) else 0.0
            cvar_i = -np.mean(sorted_ret[:idx]) if idx > 0 else var_i
            std_i = np.std(returns)

            total_var += var_i * weight
            total_cvar += cvar_i * weight
            total_std += std_i * weight
            total_return += np.mean(returns) * weight
            valid_count += 1

        if valid_count == 0:
            return PortfolioRiskMetrics(
                var_95=0.0,
                var_99=0.0,
                cvar_95=0.0,
                std_dev=0.0,
                expected_return=0.0,
                risk_adj_factor=1.0,
                simulation_count=0,
                status="DATA_INSUFFICIENT",
            )

        var_pct = total_var * 100
        if var_pct >= 5.0:
            risk_adj = 0.5
        elif var_pct >= 3.0:
            risk_adj = 0.75
        elif var_pct >= 1.5:
            risk_adj = 0.9
        else:
            risk_adj = 1.0

        return PortfolioRiskMetrics(
            var_95=total_var,
            var_99=total_var * 1.2,  # 대략적 추정
            cvar_95=total_cvar,
            std_dev=total_std,
            expected_return=total_return,
            risk_adj_factor=risk_adj,
            simulation_count=0,
            status="DATA_INSUFFICIENT",
        )
