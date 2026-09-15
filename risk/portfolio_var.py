"""
risk/portfolio_var.py - v2.1 (Session 45: mypy strict 적용)

v2.0 → v2.1 변경 사항 (mypy strict 오류 12개 해결, 실제 mypy 출력 줄 번호 기준):
    - 48번(kelly_meta 필드), 53/54번(returns_dict/weights 파라미터),
      133번(_compute_kelly 반환), 164/165번(calculate의 returns_dict/weights),
      291번(_fallback_individual_var의 returns_dict/weights):
      dict -> Dict[str, List[float]] / Dict[str, float] / Dict[str, Any]
    - 106번: __init__의 var_calculator=None 파라미터에 Optional[Any] 명시,
      반환 타입 -> None 추가 (no-untyped-def 해결)
    - 126번: _get_var_calculator(self) -> Any 반환 타입 명시
      (no-untyped-def + 144번 no-untyped-call 동시 해결)
    - 145번: calculate_kelly()가 Dict[str, Any] 반환으로 확정되어 자동 해소
      (var_calculator.py v2.1 수정의 전이 효과)
    - 203번: _fallback_individual_var 반환 타입 -> PortfolioRiskMetrics 명시
    - 그 외 로직/동작 100% 무변경

v2.0 (기존 유지):
    Monte Carlo 기반 포트폴리오 VaR / CVaR + Kelly Criterion 통합
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, cast

import numpy as np

from observability.tracer import get_tracer

logger = logging.getLogger(__name__)
trace = get_tracer(__name__)


@dataclass
class PortfolioRiskMetrics:
    """포트폴리오 리스크 지표 (v2.0 Kelly 통합)"""

    var_95: float
    var_99: float
    cvar_95: float
    std_dev: float
    expected_return: float
    risk_adj_factor: float
    simulation_count: int
    status: str
    kelly_position_limit: float = 1.0
    position_limit: float = 1.0
    kelly_win_rate: float = 0.0
    kelly_valid: bool = False
    kelly_meta: Dict[str, Any] = field(default_factory=dict)


def _calc_portfolio_returns(
    tickers: List[str],
    returns_dict: Dict[str, List[float]],
    weights: Dict[str, float],
) -> List[float]:
    """종목별 수익률 × 가중치 합산 → 포트폴리오 일별 수익률."""
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
    """VaR % 기준 리스크 조정 계수 산출."""
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
        var_calculator: Optional[Any] = None,
    ) -> None:
        self.confidence = confidence
        self.num_simulations = num_simulations
        self.lookback_days = lookback_days
        self._var_calculator = var_calculator

    def _get_var_calculator(self) -> Any:
        """VaRCalculator lazy getter — 순환 임포트 방지."""
        if self._var_calculator is None:
            from risk.var_calculator import VaRCalculator  # noqa: PLC0415
            self._var_calculator = VaRCalculator(confidence=self.confidence)
        return self._var_calculator

    def _compute_kelly(self, portfolio_returns: List[float], var_estimate: float = 0.0) -> Dict[str, Any]:
        """포트폴리오 합산 수익률로 Kelly fraction 계산."""
        try:
            vc = self._get_var_calculator()
            return cast(dict[str, Any], vc.calculate_kelly(portfolio_returns, var=var_estimate))
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
        returns_dict: Dict[str, List[float]],
        weights: Dict[str, float],
    ) -> PortfolioRiskMetrics:
        """포트폴리오 VaR + Kelly 통합 계산."""
        if not tickers:
            return PortfolioRiskMetrics(
                var_95=0.0, var_99=0.0, cvar_95=0.0, std_dev=0.0,
                expected_return=0.0, risk_adj_factor=1.0,
                simulation_count=0, status="NO_ASSETS",
            )

        total_weight = sum(weights.values())
        if total_weight == 0:
            return PortfolioRiskMetrics(
                var_95=0.0, var_99=0.0, cvar_95=0.0, std_dev=0.0,
                expected_return=0.0, risk_adj_factor=1.0,
                simulation_count=0, status="NO_WEIGHT",
            )
        normalized_weights = {t: w / total_weight for t, w in weights.items()}

        min_len = min(
            len(returns_dict.get(t, [])) for t in tickers if t in returns_dict
        )
        if min_len < 30:
            logger.warning(
                "⚠️ 포트폴리오 VaR: 데이터 부족 (최소 %d일, 30일 필요) → 개별 VaR 합산", min_len
            )
            return cast(PortfolioRiskMetrics, self._fallback_individual_var(returns_dict, normalized_weights))

        aligned_returns = []
        for t in tickers:
            ret = returns_dict.get(t, [])
            if len(ret) > min_len:
                ret = ret[-min_len:]
            aligned_returns.append(ret)

        returns_matrix = np.array(aligned_returns).T
        mean_returns = np.mean(returns_matrix, axis=0)
        cov_matrix = np.cov(returns_matrix, rowvar=False)
        weight_array = np.array([normalized_weights.get(t, 0.0) for t in tickers])

        portfolio_mean = float(np.dot(weight_array, mean_returns))
        portfolio_var_val = float(np.dot(weight_array.T, np.dot(cov_matrix, weight_array)))
        portfolio_std = float(np.sqrt(portfolio_var_val)) if portfolio_var_val > 0 else 0.0

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

        sorted_returns = np.sort(simulated_returns)
        var_95_idx = int(self.confidence * self.num_simulations)
        var_99_idx = int(0.99 * self.num_simulations)

        var_95 = float(-sorted_returns[var_95_idx]) if var_95_idx < len(sorted_returns) else 0.0
        var_99 = float(-sorted_returns[var_99_idx]) if var_99_idx < len(sorted_returns) else 0.0

        tail_returns = sorted_returns[:var_95_idx]
        cvar_95 = float(-np.mean(tail_returns)) if len(tail_returns) > 0 else var_95

        risk_adj = _calc_risk_adj_factor(var_95 * 100)

        portfolio_returns = _calc_portfolio_returns(tickers, returns_dict, normalized_weights)
        kelly_result = self._compute_kelly(portfolio_returns, var_estimate=var_95)
        kelly_pos_limit = kelly_result["position_limit"]
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
        self, returns_dict: Dict[str, List[float]], weights: Dict[str, float]
    ) -> PortfolioRiskMetrics:
        """데이터 부족 시 개별 VaR의 가중 합산으로 포트폴리오 VaR 추정."""
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
            all_returns.extend([r * weight for r in returns])
            valid_count += 1

        if valid_count == 0:
            return PortfolioRiskMetrics(
                var_95=0.0, var_99=0.0, cvar_95=0.0, std_dev=0.0,
                expected_return=0.0, risk_adj_factor=1.0,
                simulation_count=0, status="DATA_INSUFFICIENT",
            )

        risk_adj = _calc_risk_adj_factor(total_var * 100)
        kelly_result = self._compute_kelly(all_returns, var_estimate=total_var)
        kelly_pos_limit = kelly_result["position_limit"]
        final_position_limit = min(risk_adj, kelly_pos_limit)

        return PortfolioRiskMetrics(
            var_95=total_var,
            var_99=total_var * 1.2,
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