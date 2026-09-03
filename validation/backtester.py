"""
validation/backtester.py - v9.0 (Session 13)

Backtester 고도화 + Walk-Forward 자동화
- WalkForwardEngine: Anchored / Rolling 두 가지 모드
- 성과 지표: Sharpe / Sortino / Profit Factor / Calmar / MAR Ratio
- 순수 Python (numpy/scipy 불필요), 모든 지표는 0-나눔/inf 안전 처리
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logger import setup_logger

logger = setup_logger("backtester")

_RISK_FREE_RATE = 0.0
_ANNUALIZATION = math.sqrt(252)
_METRIC_CAP = 999.0  # inf 대신 사용하는 안전 상한값 (JSON 직렬화 안전)


# ═══════════════════════════════════════════════════════════════════
#  순수 Python 수학 헬퍼
# ═══════════════════════════════════════════════════════════════════

def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: List[float], ddof: int = 1) -> float:
    n = len(values)
    if n <= ddof:
        return 0.0
    m = _mean(values)
    variance = sum((v - m) ** 2 for v in values) / (n - ddof)
    return math.sqrt(variance) if variance > 0 else 0.0


def _sharpe(returns: List[float]) -> float:
    """연간화 Sharpe Ratio. 데이터 2개 미만이면 0.0."""
    if len(returns) < 2:
        return 0.0
    m = _mean(returns) - _RISK_FREE_RATE / _ANNUALIZATION
    s = _std(returns)
    return (m / s * _ANNUALIZATION) if s > 1e-9 else 0.0


def _sortino(returns: List[float]) -> float:
    """연간화 Sortino Ratio. 하방 손실이 전혀 없으면 999.0으로 캡핑."""
    if len(returns) < 2:
        return 0.0
    m = _mean(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return _METRIC_CAP if m > 0 else 0.0
    d_std = _std(downside, ddof=1)
    if d_std < 1e-9:
        return 0.0
    return (m - _RISK_FREE_RATE / _ANNUALIZATION) / d_std * _ANNUALIZATION


def _max_drawdown(equity_curve: List[float]) -> float:
    """최대 낙폭 (0~1)."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _build_equity_curve(returns: List[float], initial: float = 100.0) -> List[float]:
    curve = [initial]
    for r in returns:
        curve.append(curve[-1] * (1.0 + r))
    return curve


def _profit_factor(returns: List[float]) -> float:
    """총이익/총손실. 손실이 없으면 999.0으로 캡핑."""
    gross_profit = sum(r for r in returns if r > 0)
    gross_loss = abs(sum(r for r in returns if r < 0))
    if gross_loss < 1e-9:
        return _METRIC_CAP if gross_profit > 0 else 0.0
    return min(gross_profit / gross_loss, _METRIC_CAP)


def _calmar(total_return: float, max_dd: float, days: int) -> float:
    """Calmar Ratio = 연간 수익률 / MDD.

    MDD가 0에 가까우면서 수익이 양수인 '최상의' 경우는 Sortino/ProfitFactor와
    동일하게 999.0으로 캡핑한다 (수정: 기존 초안에서는 이 경우 0.0을 반환하여
    다른 지표와 철학이 불일치했던 버그를 여기서 통일함).
    """
    if days < 1:
        return 0.0
    annual_return = total_return * (252 / days)
    if max_dd < 1e-9:
        return _METRIC_CAP if annual_return > 0 else 0.0
    return annual_return / max_dd


def _mar(annual_return: float, max_dd: float) -> float:
    """MAR Ratio = 연간 수익률 / MDD (Calmar와 동일 정의, 로드맵 스펙 호환용 별도 필드)."""
    if max_dd < 1e-9:
        return _METRIC_CAP if annual_return > 0 else 0.0
    return annual_return / max_dd


# ═══════════════════════════════════════════════════════════════════
#  DTO
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Trade:
    """단일 거래 기록"""
    ticker: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    action: str          # "BUY" / "SELL"
    return_pct: float    # 수익률 (소수, 0.05 = +5%)

    @property
    def is_win(self) -> bool:
        return self.return_pct > 0


@dataclass
class BacktestResult:
    """단일 폴드 백테스트 결과 (compute() 호출 후 지표 필드가 채워짐)"""
    fold_id: int
    start_date: str
    end_date: str
    trades: List[Trade] = field(default_factory=list)

    total_trades: int = 0
    win_count: int = 0
    win_rate: float = 0.0
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    calmar_ratio: float = 0.0
    mar_ratio: float = 0.0
    avg_return: float = 0.0

    def compute(self) -> "BacktestResult":
        """trades 리스트를 바탕으로 모든 지표를 계산하여 필드를 채운다."""
        if not self.trades:
            return self

        returns = [t.return_pct for t in self.trades]
        self.total_trades = len(self.trades)
        self.win_count = sum(1 for t in self.trades if t.is_win)
        self.win_rate = self.win_count / self.total_trades
        self.total_return = sum(returns)
        self.avg_return = _mean(returns)
        self.sharpe_ratio = _sharpe(returns)
        self.sortino_ratio = _sortino(returns)

        equity = _build_equity_curve(returns)
        self.max_drawdown = _max_drawdown(equity)
        self.profit_factor = _profit_factor(returns)

        start = datetime.fromisoformat(self.start_date)
        end = datetime.fromisoformat(self.end_date)
        days = max((end - start).days, 1)

        self.calmar_ratio = _calmar(self.total_return, self.max_drawdown, days)
        annual_return = self.total_return * (252 / days)
        self.mar_ratio = _mar(annual_return, self.max_drawdown)
        return self

    def to_dict(self) -> dict:
        return {
            "fold_id": self.fold_id,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 4),
            "total_return": round(self.total_return, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "profit_factor": round(self.profit_factor, 4),
            "calmar_ratio": round(self.calmar_ratio, 4),
            "mar_ratio": round(self.mar_ratio, 4),
        }


@dataclass
class AggregatedResult:
    """Walk-Forward 전체 폴드 집계 결과"""
    fold_results: List[BacktestResult]
    mean_sharpe: float = 0.0
    std_sharpe: float = 0.0
    mean_win_rate: float = 0.0
    std_win_rate: float = 0.0
    calmar_ratio: float = 0.0
    sortino_ratio: float = 0.0
    profit_factor: float = 0.0
    consistency_score: float = 0.0  # 폴드 간 Sharpe 일관성 (0~1, 높을수록 안정)

    def compute(self) -> "AggregatedResult":
        if not self.fold_results:
            return self

        sharpes = [r.sharpe_ratio for r in self.fold_results]
        win_rates = [r.win_rate for r in self.fold_results]
        all_returns: List[float] = []
        for r in self.fold_results:
            all_returns.extend(t.return_pct for t in r.trades)

        self.mean_sharpe = _mean(sharpes)
        self.std_sharpe = _std(sharpes) if len(sharpes) >= 2 else 0.0
        self.mean_win_rate = _mean(win_rates)
        self.std_win_rate = _std(win_rates) if len(win_rates) >= 2 else 0.0

        if all_returns:
            self.sortino_ratio = _sortino(all_returns)
            self.profit_factor = _profit_factor(all_returns)
            equity = _build_equity_curve(all_returns)
            max_dd = _max_drawdown(equity)
            total_ret = sum(all_returns)
            total_trades = sum(r.total_trades for r in self.fold_results)
            days = max(total_trades, 1)
            self.calmar_ratio = _calmar(total_ret, max_dd, days)

        if len(sharpes) >= 2:
            max_std = max(abs(self.mean_sharpe), 1.0)
            self.consistency_score = max(0.0, 1.0 - self.std_sharpe / max_std)
        else:
            self.consistency_score = 1.0  # 폴드 1개 → 비교 불가, 만점 처리

        return self

    def to_dict(self) -> dict:
        return {
            "fold_count": len(self.fold_results),
            "mean_sharpe": round(self.mean_sharpe, 4),
            "std_sharpe": round(self.std_sharpe, 4),
            "mean_win_rate": round(self.mean_win_rate, 4),
            "std_win_rate": round(self.std_win_rate, 4),
            "calmar_ratio": round(self.calmar_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "profit_factor": round(self.profit_factor, 4),
            "consistency_score": round(self.consistency_score, 4),
            "folds": [r.to_dict() for r in self.fold_results],
        }

    def summary_text(self) -> str:
        """텔레그램 리포트용 텍스트 요약"""
        lines = [
            "📊 Walk-Forward 백테스트 요약",
            f"  폴드 수: {len(self.fold_results)}",
            f"  평균 Sharpe: {self.mean_sharpe:.3f} (±{self.std_sharpe:.3f})",
            f"  평균 승률:   {self.mean_win_rate:.1%} (±{self.std_win_rate:.1%})",
            f"  Sortino:     {self.sortino_ratio:.3f}",
            f"  Profit Factor: {self.profit_factor:.2f}",
            f"  Calmar:      {self.calmar_ratio:.3f}",
            f"  일관성 점수: {self.consistency_score:.3f}",
        ]
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  WalkForwardEngine
# ═══════════════════════════════════════════════════════════════════

class WalkForwardEngine:
    """
    Walk-Forward 백테스트 엔진.

    "rolling": 훈련 기간이 고정 길이로 이동
    "anchored": 훈련 시작점이 항상 고정, 끝점만 확장

    사용 예::

        def my_strategy(train_dates, test_dates):
            return [Trade(...), ...]

        engine = WalkForwardEngine(train_ratio=0.7, min_periods=30)
        results = engine.run("005930", dates, strategy_fn=my_strategy, mode="rolling")
        agg = engine.aggregate_results(results)
    """

    def __init__(self, train_ratio: float = 0.7, min_periods: int = 30) -> None:
        if not (0 < train_ratio < 1):
            raise ValueError(f"train_ratio must be in (0, 1), got {train_ratio}")
        if min_periods < 2:
            raise ValueError(f"min_periods must be >= 2, got {min_periods}")
        self._train_ratio = train_ratio
        self._min_periods = min_periods

    def _split_periods(
        self, dates: List[str], mode: str
    ) -> List[Tuple[List[str], List[str]]]:
        n = len(dates)
        train_size = max(self._min_periods, int(n * self._train_ratio))
        test_size = max(1, n - train_size)
        if train_size + test_size > n:
            return []

        folds: List[Tuple[List[str], List[str]]] = []
        step = max(1, test_size // 3)
        pos = train_size
        while pos < n:
            test_end = min(pos + step, n)
            if mode == "anchored":
                train_start = 0
            else:
                train_start = max(0, pos - train_size)
            folds.append((dates[train_start:pos], dates[pos:test_end]))
            pos = test_end
        return folds

    def run(
        self,
        ticker: str,
        dates: List[str],
        strategy_fn: Callable[[List[str], List[str]], List[Trade]],
        mode: str = "rolling",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[BacktestResult]:
        if mode not in ("rolling", "anchored"):
            raise ValueError(f"mode must be 'rolling' or 'anchored', got '{mode}'")

        folds = self._split_periods(dates, mode)
        if not folds:
            logger.warning(
                f"[{ticker}] 데이터 부족으로 Walk-Forward 폴드 생성 불가 "
                f"(dates={len(dates)}, min_periods={self._min_periods})"
            )
            return []

        results: List[BacktestResult] = []
        for fold_id, (train_dates, test_dates) in enumerate(folds):
            try:
                trades = strategy_fn(train_dates, test_dates)
                result = BacktestResult(
                    fold_id=fold_id,
                    start_date=test_dates[0],
                    end_date=test_dates[-1],
                    trades=trades,
                )
                result.compute()
                results.append(result)
                logger.debug(
                    f"[{ticker}] fold={fold_id} trades={len(trades)} "
                    f"wr={result.win_rate:.1%} sharpe={result.sharpe_ratio:.3f}"
                )
            except Exception as e:
                logger.warning(f"[{ticker}] fold={fold_id} 실패: {e}")
                results.append(BacktestResult(
                    fold_id=fold_id,
                    start_date=test_dates[0] if test_dates else "unknown",
                    end_date=test_dates[-1] if test_dates else "unknown",
                ))
        return results

    def aggregate_results(self, results: List[BacktestResult]) -> AggregatedResult:
        agg = AggregatedResult(fold_results=results)
        return agg.compute()


# ═══════════════════════════════════════════════════════════════════
#  Backtester (통합 인터페이스)
# ═══════════════════════════════════════════════════════════════════

class Backtester:
    """단순 백테스트 + Walk-Forward 통합 인터페이스."""

    def __init__(self, train_ratio: float = 0.7, min_periods: int = 30) -> None:
        self._engine = WalkForwardEngine(train_ratio, min_periods)

    def run_simple(
        self, trades: List[Trade], start_date: str, end_date: str, fold_id: int = 0
    ) -> BacktestResult:
        """단순 백테스트 (Walk-Forward 없이 전체 기간 단일 평가)."""
        result = BacktestResult(
            fold_id=fold_id, start_date=start_date, end_date=end_date, trades=trades
        )
        return result.compute()

    def run_walk_forward(
        self,
        ticker: str,
        dates: List[str],
        strategy_fn: Callable[[List[str], List[str]], List[Trade]],
        mode: str = "rolling",
    ) -> AggregatedResult:
        results = self._engine.run(ticker, dates, strategy_fn, mode)
        return self._engine.aggregate_results(results)

    def generate_report(self, result: BacktestResult) -> str:
        lines = [
            f"📈 백테스트 결과 [fold={result.fold_id}]",
            f"  기간: {result.start_date} ~ {result.end_date}",
            f"  거래 수:       {result.total_trades}",
            f"  승률:          {result.win_rate:.1%}",
            f"  총 수익률:     {result.total_return:.2%}",
            f"  Sharpe:        {result.sharpe_ratio:.3f}",
            f"  Sortino:       {result.sortino_ratio:.3f}",
            f"  MDD:           {result.max_drawdown:.1%}",
            f"  Profit Factor: {result.profit_factor:.2f}",
            f"  Calmar:        {result.calmar_ratio:.3f}",
        ]
        return "\n".join(lines)
