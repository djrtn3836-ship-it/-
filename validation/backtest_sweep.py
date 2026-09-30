# -*- coding: utf-8 -*-
"""validation/backtest_sweep.py - 다종목 일괄 백테스트 + 파라미터 스윕.

배경:
    단일 종목·고정 파라미터 백테스트는 표본이 작아 판단 근거로 부족하다.
    본 모듈은 190종목 전체를 대상으로 **앙상블 임계값 / 청산 규칙**을 격자 탐색해
    포트폴리오 수준 통계로 순위를 매긴다.

핵심 최적화:
    전략 호출(Trend/Reversal/Breakout)이 가장 비싸므로 종목별 **net score를 1회만**
    계산해 캐시한다(`EnsembleEvaluator.net_scores`). 이후 임계값 변경은 단순 비교,
    청산 규칙 변경은 저렴한 시뮬레이션이므로 수십 개 조합을 빠르게 평가할 수 있다.

사용:
    python -m validation.backtest_sweep --limit 190 --start 2024-10-01 --end 2026-09-30
    python -m validation.backtest_sweep --limit 190 --start ... --end ... --sweep
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from core.logger import setup_logger
from validation.backtest_runner import SimConfig, simulate_entries
from validation.backtester import AggregatedResult, WalkForwardEngine
from validation.strategy_backtest import EnsembleEvaluator, _edge_indices, scores_to_actions

logger = setup_logger("backtest_sweep")


# ═══════════════════════════════════════════════════════════════════
#  데이터 구조
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TickerSeries:
    """종목 1개의 봉 + 캐시된 앙상블 net score."""

    ticker: str
    bars: List[Dict[str, Any]]
    net_scores: List[Optional[float]]
    date_to_idx: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.date_to_idx:
            self.date_to_idx = {str(b.get("date", "")): i for i, b in enumerate(self.bars)}

    @property
    def dates(self) -> List[str]:
        return [str(b.get("date", "")) for b in self.bars]


@dataclass
class BatchMetrics:
    """여러 종목 집계 지표."""

    tickers: int = 0
    evaluated: int = 0
    total_trades: int = 0
    mean_sharpe: float = 0.0
    median_sharpe: float = 0.0
    mean_win_rate: float = 0.0
    mean_profit_factor: float = 0.0
    profitable_pct: float = 0.0  # mean_sharpe > 0 종목 비율

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tickers": self.tickers,
            "evaluated": self.evaluated,
            "total_trades": self.total_trades,
            "mean_sharpe": round(self.mean_sharpe, 4),
            "median_sharpe": round(self.median_sharpe, 4),
            "mean_win_rate": round(self.mean_win_rate, 4),
            "mean_profit_factor": round(self.mean_profit_factor, 4),
            "profitable_pct": round(self.profitable_pct, 4),
        }

    def summary(self) -> str:
        return (
            f"평가 {self.evaluated}/{self.tickers}종목 · 거래 {self.total_trades}건 | "
            f"평균 Sharpe(pooled) {self.mean_sharpe:+.3f} (중앙 {self.median_sharpe:+.3f}) | "
            f"평균 승률 {self.mean_win_rate:.1%} | 평균 PF {self.mean_profit_factor:.2f} | "
            f"수익 종목 {self.profitable_pct:.1%}"
        )


@dataclass
class SweepRow:
    """파라미터 조합 1개의 평가 결과."""

    threshold: float
    sim: SimConfig
    metrics: BatchMetrics

    def label(self) -> str:
        return (
            f"thr={self.threshold:.2f} hold={self.sim.hold_days}d "
            f"stop={self.sim.stop_loss_pct:.0%} tp={self.sim.take_profit_pct:.0%}"
        )


@dataclass
class OOSRow:
    """In-Sample 최적 조합을 Out-of-Sample에서 재평가한 결과."""

    threshold: float
    sim: SimConfig
    is_metrics: BatchMetrics
    oos_metrics: BatchMetrics

    def label(self) -> str:
        return (
            f"thr={self.threshold:.2f} hold={self.sim.hold_days}d "
            f"stop={self.sim.stop_loss_pct:.0%} tp={self.sim.take_profit_pct:.0%}"
        )

    def median_drop(self) -> float:
        """중앙 Sharpe 변화량(IS → OOS)."""
        return self.oos_metrics.median_sharpe - self.is_metrics.median_sharpe


# ═══════════════════════════════════════════════════════════════════
#  준비 / 평가
# ═══════════════════════════════════════════════════════════════════

async def prepare_series(
    db: Any,
    tickers: Sequence[str],
    start_date: str,
    end_date: str,
    evaluator: Optional[EnsembleEvaluator] = None,
    min_bars: int = 120,
) -> List[TickerSeries]:
    """종목별 OHLCV 로드 + net score 1회 계산(캐시)."""
    ev = evaluator or EnsembleEvaluator()
    out: List[TickerSeries] = []
    for i, ticker in enumerate(tickers, 1):
        rows = await db.get_ohlcv_range(ticker, start_date, end_date)
        bars = [r for r in rows if float(r.get("close", 0.0) or 0.0) > 0]
        if len(bars) < min_bars:
            logger.debug(f"[{i}/{len(tickers)}] {ticker}: 봉 부족({len(bars)}) — 건너뜀")
            continue
        scores = await ev.net_scores(bars)
        out.append(TickerSeries(ticker=ticker, bars=bars, net_scores=scores))
        if i % 20 == 0:
            logger.info(f"  준비 {i}/{len(tickers)}종목 (유효 {len(out)})")
    logger.info(f"시리즈 준비 완료: {len(out)}/{len(tickers)}종목")
    return out


def _evaluate_ticker(
    ts: TickerSeries,
    threshold: float,
    sim_config: SimConfig,
    train_ratio: float = 0.7,
    min_periods: int = 30,
) -> Optional[AggregatedResult]:
    """단일 종목을 주어진 파라미터로 Walk-Forward 평가한다."""
    actions = scores_to_actions(ts.net_scores, threshold)
    dates = ts.dates
    date_to_idx = ts.date_to_idx

    def strategy_fn(_train: List[str], test_dates: List[str]) -> List[Any]:
        if not test_dates:
            return []
        first = date_to_idx.get(test_dates[0])
        last = date_to_idx.get(test_dates[-1])
        if first is None or last is None:
            return []
        window = ts.bars[first : last + 1]
        local_entries = _edge_indices(actions[first : last + 1], offset=0)
        return simulate_entries(window, local_entries, sim_config, ticker=ts.ticker)

    engine = WalkForwardEngine(train_ratio=train_ratio, min_periods=min_periods)
    results = engine.run(ts.ticker, dates, strategy_fn, mode="rolling")
    if not results:
        return None
    return engine.aggregate_results(results)


def evaluate_combo(
    series_list: Sequence[TickerSeries],
    threshold: float,
    sim_config: SimConfig,
    train_ratio: float = 0.7,
    min_periods: int = 30,
) -> BatchMetrics:
    """파라미터 조합 1개를 전체 종목에 적용해 집계 지표를 산출한다."""
    sharpes: List[float] = []
    win_rates: List[float] = []
    profit_factors: List[float] = []
    total_trades = 0
    evaluated = 0

    for ts in series_list:
        agg = _evaluate_ticker(ts, threshold, sim_config, train_ratio, min_periods)
        if agg is None or not agg.fold_results:
            continue
        evaluated += 1
        total_trades += sum(r.total_trades for r in agg.fold_results)
        sharpes.append(agg.pooled_sharpe)
        win_rates.append(agg.mean_win_rate)
        profit_factors.append(agg.profit_factor)

    metrics = BatchMetrics(
        tickers=len(series_list),
        evaluated=evaluated,
        total_trades=total_trades,
    )
    if sharpes:
        metrics.mean_sharpe = statistics.fmean(sharpes)
        metrics.median_sharpe = statistics.median(sharpes)
        metrics.mean_win_rate = statistics.fmean(win_rates)
        metrics.mean_profit_factor = statistics.fmean(profit_factors)
        metrics.profitable_pct = sum(1 for s in sharpes if s > 0) / len(sharpes)
    return metrics


def run_sweep(
    series_list: Sequence[TickerSeries],
    thresholds: Sequence[float],
    sim_configs: Sequence[SimConfig],
    train_ratio: float = 0.7,
    min_periods: int = 30,
) -> List[SweepRow]:
    """격자 탐색 후 **중앙 Sharpe** 내림차순(외란에 강건)으로 정렬된 결과를 반환.

    평균 Sharpe는 소수 종목의 극단값(거래 수가 적어 표준편차가 작을 때)에
    크게 흔들리므로, 순위는 중앙값을 1순위로 삼고 평균·거래 수를 보조로 쓴다.
    """
    rows: List[SweepRow] = []
    for threshold in thresholds:
        for sim in sim_configs:
            metrics = evaluate_combo(series_list, threshold, sim, train_ratio, min_periods)
            rows.append(SweepRow(threshold=threshold, sim=sim, metrics=metrics))
    rows.sort(
        key=lambda r: (r.metrics.median_sharpe, r.metrics.mean_sharpe, r.metrics.total_trades),
        reverse=True,
    )
    return rows


DEFAULT_THRESHOLDS: Sequence[float] = (0.15, 0.20, 0.25, 0.30, 0.35)
DEFAULT_SIM_CONFIGS: Sequence[SimConfig] = tuple(
    SimConfig(hold_days=hold, stop_loss_pct=stop, take_profit_pct=tp)
    for hold in (3, 5, 10)
    for stop in (0.05, 0.08)
    for tp in (0.10, 0.15)
)


def slice_series(
    ts: TickerSeries, start_date: str, end_date: str, min_bars: int = 90
) -> Optional[TickerSeries]:
    """종목 시리즈를 날짜 구간으로 자른다(봉/net score 동일 마스크).

    net score는 전 구간 데이터로 이미 계산되어 있으므로(후방 참조),
    구간을 잘라도 초기 워밍업 손실 없이 그대로 사용할 수 있다.
    """
    bars: List[Dict[str, Any]] = []
    scores: List[Optional[float]] = []
    for bar, score in zip(ts.bars, ts.net_scores):
        d = str(bar.get("date", ""))
        if start_date <= d <= end_date:
            bars.append(bar)
            scores.append(score)
    if len(bars) < min_bars:
        return None
    return TickerSeries(ticker=ts.ticker, bars=bars, net_scores=scores)


def run_oos_check(
    all_series: Sequence[TickerSeries],
    is_start: str,
    split_date: str,
    oos_start: str,
    oos_end: str,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
    sim_configs: Sequence[SimConfig] = DEFAULT_SIM_CONFIGS,
    top_n: int = 5,
    train_ratio: float = 0.7,
    min_periods: int = 30,
) -> List[OOSRow]:
    """In-Sample에서 스윕 → 상위 top_n 조합을 Out-of-Sample에서 재평가.

    진짜 OOS 검증: 파라미터 선택에 쓰이지 않은 기간에서 성과가 유지되는지 확인한다.
    """
    is_series = [s for s in (slice_series(ts, is_start, split_date) for ts in all_series) if s]
    oos_series = [s for s in (slice_series(ts, oos_start, oos_end) for ts in all_series) if s]
    logger.info(f"OOS 검증 시리즈: IS {len(is_series)}종목 / OOS {len(oos_series)}종목")

    is_rows = run_sweep(is_series, thresholds, sim_configs, train_ratio, min_periods)

    oos_rows: List[OOSRow] = []
    for row in is_rows[:top_n]:
        oos_metrics = evaluate_combo(oos_series, row.threshold, row.sim, train_ratio, min_periods)
        oos_rows.append(
            OOSRow(threshold=row.threshold, sim=row.sim, is_metrics=row.metrics, oos_metrics=oos_metrics)
        )
    return oos_rows


def _resolve_tickers(limit: int) -> List[str]:
    from infrastructure.market_data.universe_provider import get_universe

    return list(get_universe().keys())[:limit]


async def _main(argv: Optional[Sequence[str]] = None) -> int:
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="다종목 일괄 백테스트 / 파라미터 스윕")
    parser.add_argument("--limit", type=int, default=190, help="유니버스에서 가져올 종목 수")
    parser.add_argument("--tickers", type=str, default="", help="쉼표 구분 종목코드(지정 시 limit 무시)")
    parser.add_argument("--start", type=str, required=True, help="시작일 YYYY-MM-DD")
    parser.add_argument("--end", type=str, required=True, help="종료일 YYYY-MM-DD")
    parser.add_argument("--sweep", action="store_true", help="파라미터 격자 탐색 수행")
    parser.add_argument("--top", type=int, default=10, help="스윕 상위 N개 출력")
    parser.add_argument(
        "--thresholds",
        type=str,
        default="",
        help="쉼표 구분 앙상블 임계값 목록 (기본 0.15,0.20,0.25,0.30,0.35)",
    )
    parser.add_argument(
        "--holds",
        type=str,
        default="",
        help="쉼표 구분 보유기간(일) 목록 (기본 3,5,10)",
    )
    parser.add_argument(
        "--oos-split",
        type=str,
        default="",
        help="OOS 분리 기준일 YYYY-MM-DD (지정 시 IS에서 튜닝 → OOS에서 재검증)",
    )
    args = parser.parse_args(list(argv[1:]) if argv else None)

    tickers = (
        [t.strip() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else _resolve_tickers(args.limit)
    )

    from data.db_manager import DatabaseManager

    db = DatabaseManager()
    await db.init_db()
    started = time.time()
    try:
        series_list = await prepare_series(db, tickers, args.start, args.end)
        if not series_list:
            print("유효한 종목이 없습니다 (OHLCV 부족)")
            return 1

        thresholds = (
            tuple(float(x) for x in args.thresholds.split(",") if x.strip())
            if args.thresholds
            else DEFAULT_THRESHOLDS
        )
        if args.holds:
            holds = tuple(int(x) for x in args.holds.split(",") if x.strip())
            sim_configs = tuple(
                SimConfig(hold_days=h, stop_loss_pct=s, take_profit_pct=t)
                for h in holds
                for s in (0.05, 0.08)
                for t in (0.10, 0.15)
            )
        else:
            sim_configs = DEFAULT_SIM_CONFIGS

        if args.oos_split:
            from datetime import date as _date, timedelta as _timedelta

            split = args.oos_split
            oos_start = (_date.fromisoformat(split) + _timedelta(days=1)).isoformat()
            print(f"OOS 검증: IS [{args.start} ~ {split}] → OOS [{oos_start} ~ {args.end}]")
            print(f"  임계값 {len(thresholds)} × 청산규칙 {len(sim_configs)} = "
                  f"{len(thresholds) * len(sim_configs)}조합 (IS 튜닝 → 상위 5개 OOS 재검증)\n")
            oos_rows = run_oos_check(
                series_list,
                is_start=args.start,
                split_date=split,
                oos_start=oos_start,
                oos_end=args.end,
                thresholds=thresholds,
                sim_configs=sim_configs,
                top_n=5,
            )
            print(f"{'#':<3} {'파라미터':<34} {'IS 중앙 Sharpe':>14} {'OOS 중앙 Sharpe':>15} {'변화':>8}")
            for i, row in enumerate(oos_rows, 1):
                print(
                    f"{i:<3} {row.label():<34} "
                    f"{row.is_metrics.median_sharpe:>+14.3f} {row.oos_metrics.median_sharpe:>+15.3f} "
                    f"{row.median_drop():>+8.3f}"
                )
            if oos_rows:
                best = oos_rows[0]
                print(f"\nIS 1위 조합 OOS 결과: {best.label()}")
                print(f"  IS : {best.is_metrics.summary()}")
                print(f"  OOS: {best.oos_metrics.summary()}")
        elif not args.sweep:
            metrics = evaluate_combo(series_list, 0.25, SimConfig())
            print(f"기준 파라미터(thr=0.25, hold=5d, stop=5%, tp=10%)")
            print(f"  {metrics.summary()}")
        else:
            print(f"스윕 조합: 임계값 {len(thresholds)} × 청산규칙 {len(sim_configs)} "
                  f"= {len(thresholds) * len(sim_configs)}개")
            rows = run_sweep(series_list, thresholds, sim_configs)
            print(f"\n{'순위':<4} {'파라미터':<34} 지표")
            for rank, row in enumerate(rows[: args.top], 1):
                print(f"{rank:<4} {row.label():<34} {row.metrics.summary()}")
            best = rows[0]
            print(f"\n🏆 최적(중앙 Sharpe 기준): {best.label()}")
            print(f"   {best.metrics.summary()}")
    finally:
        await db.close()

    print(f"\n소요: {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
