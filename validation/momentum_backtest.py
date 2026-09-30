# -*- coding: utf-8 -*-
"""validation/momentum_backtest.py - 횡단면 모멘텀(상대강도) 전략 백테스터.

배경:
    기존 앙상블(Trend/Reversal/Breakout) 신호는 롤링 OOS에서 견고한 엣지가 없었다
    (DEVELOPMENT_LOG P2-10). 모멘텀(상대강도)은 횡단면 전략으로, **종목 간 상대 순위**를
    이용하므로 개별 종목 신호와는 다른 수익 원천을 갖는다.

전략:
    매 리밸런싱 시점마다
      1) 각 종목의 trailing lookback일 수익률(모멘텀) 계산
      2) 상위 top_k 종목을 동일가중 매수
      3) hold_days 보유 후 다음 리밸런싱
    → 기간별 포트폴리오 수익률 시계열을 만들어 지표를 산출한다.

평가:
    - IS(학습) 구간에서 lookback/top_k/hold_days를 탐색 → OOS(검증) 구간에서 재평가
    - 지표: 연환산 Sharpe, CAGR, MDD, 승률, 기간 수

사용:
    python -m validation.momentum_backtest --limit 190 --start 2021-10-01 --end 2026-09-30
    python -m validation.momentum_backtest --limit 190 --start 2021-10-01 --end 2026-09-30 --sweep
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.logger import setup_logger

logger = setup_logger("momentum_backtest")

TRADING_DAYS_PER_YEAR = 252


@dataclass
class MomentumConfig:
    lookback: int = 60
    top_k: int = 10
    hold_days: int = 20


@dataclass
class MomentumMetrics:
    periods: int = 0
    total_return: float = 0.0
    cagr: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_period_return: float = 0.0

    def summary(self) -> str:
        return (
            f"기간 {self.periods}회 | 총수익 {self.total_return:+.1%} | CAGR {self.cagr:+.1%} | "
            f"Sharpe {self.sharpe:+.2f} | MDD {self.max_drawdown:.1%} | 승률 {self.win_rate:.1%}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "periods": self.periods,
            "total_return": round(self.total_return, 4),
            "cagr": round(self.cagr, 4),
            "sharpe": round(self.sharpe, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "win_rate": round(self.win_rate, 4),
            "avg_period_return": round(self.avg_period_return, 4),
        }


# ═══════════════════════════════════════════════════════════════════
#  핵심 로직 (순수 함수)
# ═══════════════════════════════════════════════════════════════════

def build_price_panel(
    series: Dict[str, Dict[str, float]], dates: Sequence[str]
) -> List[Dict[str, float]]:
    """ticker -> {date: close} 를 날짜축에 정렬된 패널(리스트)로 변환한다."""
    panel: List[Dict[str, float]] = []
    for d in dates:
        row: Dict[str, float] = {}
        for ticker, prices in series.items():
            px = prices.get(d)
            if px and px > 0:
                row[ticker] = px
        panel.append(row)
    return panel


def select_top_k(momentum: Dict[str, float], top_k: int) -> List[str]:
    """모멘텀 내림차순 상위 top_k 종목."""
    ranked = sorted(momentum.items(), key=lambda kv: kv[1], reverse=True)
    return [t for t, _ in ranked[:top_k]]


def momentum_returns(
    dates: Sequence[str],
    panel: Sequence[Dict[str, float]],
    config: MomentumConfig,
) -> List[float]:
    """리밸런싱 주기별 포트폴리오 수익률 시계열을 만든다."""
    n = len(dates)
    returns: List[float] = []
    i = config.lookback
    while i + config.hold_days < n:
        base_row, cur_row, future_row = panel[i - config.lookback], panel[i], panel[i + config.hold_days]

        momentum: Dict[str, float] = {}
        for ticker, price in cur_row.items():
            past = base_row.get(ticker)
            if past and past > 0:
                momentum[ticker] = price / past - 1.0
        if not momentum:
            i += config.hold_days
            continue

        picks = select_top_k(momentum, config.top_k)
        period_rets: List[float] = []
        for t in picks:
            p0 = cur_row.get(t)
            p1 = future_row.get(t)
            if p0 and p1 and p0 > 0:
                period_rets.append(p1 / p0 - 1.0)
        if period_rets:
            returns.append(sum(period_rets) / len(period_rets))  # 동일가중
        i += config.hold_days
    return returns


def compute_metrics(returns: Sequence[float], hold_days: int) -> MomentumMetrics:
    """기간 수익률 시계열 → 성과 지표."""
    m = MomentumMetrics(periods=len(returns))
    if not returns:
        return m

    equity = 1.0
    curve = [1.0]
    for r in returns:
        equity *= 1.0 + r
        curve.append(equity)
    m.total_return = equity - 1.0

    periods_per_year = TRADING_DAYS_PER_YEAR / max(hold_days, 1)
    years = len(returns) / periods_per_year if periods_per_year > 0 else 0
    m.cagr = ((equity) ** (1.0 / years) - 1.0) if years > 0 and equity > 0 else 0.0

    mean = statistics.fmean(returns)
    std = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    if std > 1e-12:
        m.sharpe = max(-999.0, min(999.0, mean / std * math.sqrt(periods_per_year)))

    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)
    m.max_drawdown = max_dd
    m.win_rate = sum(1 for r in returns if r > 0) / len(returns)
    m.avg_period_return = mean
    return m


@dataclass
class MomentumResult:
    config: MomentumConfig
    is_metrics: MomentumMetrics
    oos_metrics: Optional[MomentumMetrics] = None

    def label(self) -> str:
        return f"lb={self.config.lookback} k={self.config.top_k} hold={self.config.hold_days}"


# ═══════════════════════════════════════════════════════════════════
#  데이터 로딩 / 실행
# ═══════════════════════════════════════════════════════════════════

async def load_price_series(
    db: Any, tickers: Sequence[str], start_date: str, end_date: str, min_bars: int = 120
) -> Tuple[Dict[str, Dict[str, float]], List[str]]:
    """전 종목 종가 시계열과 공통 날짜축을 만든다."""
    series: Dict[str, Dict[str, float]] = {}
    date_set: set[str] = set()
    for i, ticker in enumerate(tickers, 1):
        rows = await db.get_ohlcv_range(ticker, start_date, end_date)
        prices = {
            str(r["date"]): float(r["close"])
            for r in rows
            if r.get("close") and float(r["close"]) > 0
        }
        if len(prices) < min_bars:
            continue
        series[ticker] = prices
        date_set.update(prices.keys())
        if i % 40 == 0:
            logger.info(f"  로드 {i}/{len(tickers)} (유효 {len(series)})")
    dates = sorted(date_set)
    logger.info(f"가격 패널: {len(series)}종목 × {len(dates)}일")
    return series, dates


def evaluate_config(
    dates: Sequence[str],
    panel: Sequence[Dict[str, float]],
    config: MomentumConfig,
) -> MomentumMetrics:
    return compute_metrics(momentum_returns(dates, panel, config), config.hold_days)


def sweep_momentum(
    dates: Sequence[str],
    panel: Sequence[Dict[str, float]],
    lookbacks: Sequence[int] = (20, 60, 120),
    top_ks: Sequence[int] = (5, 10, 20),
    holds: Sequence[int] = (10, 20, 40),
) -> List[MomentumResult]:
    """IS 구간 격자 탐색(Sharpe 내림차순)."""
    results: List[MomentumResult] = []
    for lb in lookbacks:
        for k in top_ks:
            for h in holds:
                cfg = MomentumConfig(lookback=lb, top_k=k, hold_days=h)
                metrics = evaluate_config(dates, panel, cfg)
                results.append(MomentumResult(config=cfg, is_metrics=metrics))
    results.sort(key=lambda r: r.is_metrics.sharpe, reverse=True)
    return results


def _resolve_tickers(limit: int) -> List[str]:
    from infrastructure.market_data.universe_provider import get_universe

    return list(get_universe().keys())[:limit]


async def _main(argv: Optional[Sequence[str]] = None) -> int:
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="횡단면 모멘텀 전략 백테스트")
    parser.add_argument("--limit", type=int, default=190)
    parser.add_argument("--tickers", type=str, default="")
    parser.add_argument("--start", type=str, required=True)
    parser.add_argument("--end", type=str, required=True)
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--hold", type=int, default=20)
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--oos-split", type=str, default="", help="OOS 분리 기준일")
    parser.add_argument("--top", type=int, default=10)
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
        series, dates = await load_price_series(db, tickers, args.start, args.end)
        if len(series) < 5 or len(dates) < 120:
            print("데이터 부족: 종목/날짜가 너무 적습니다")
            return 1
        panel = build_price_panel(series, dates)

        if args.oos_split:
            split = args.oos_split
            is_dates = [d for d in dates if d <= split]
            oos_dates = [d for d in dates if d > split]
            is_panel = build_price_panel(series, is_dates)
            oos_panel = build_price_panel(series, oos_dates)

            print(f"OOS: IS [{args.start}~{split}] {len(is_dates)}일 → OOS [{split}~{args.end}] {len(oos_dates)}일")
            is_rows = sweep_momentum(is_dates, is_panel)
            print(f"\nIS 상위 {args.top}개:")
            for i, r in enumerate(is_rows[: args.top], 1):
                print(f"  {i:>2}. {r.label():<26} {r.is_metrics.summary()}")

            print(f"\n상위 5개 OOS 재평가:")
            print(f"{'#':<3} {'파라미터':<26} {'IS Sharpe':>10} {'OOS Sharpe':>11} {'OOS CAGR':>10} {'OOS MDD':>9}")
            for i, r in enumerate(is_rows[:5], 1):
                oos = evaluate_config(oos_dates, oos_panel, r.config)
                r.oos_metrics = oos
                print(
                    f"{i:<3} {r.label():<26} {r.is_metrics.sharpe:>+10.2f} {oos.sharpe:>+11.2f} "
                    f"{oos.cagr:>+10.1%} {oos.max_drawdown:>9.1%}"
                )
            if is_rows:
                print(f"\nIS 1위 OOS: {is_rows[0].oos_metrics.summary() if is_rows[0].oos_metrics else 'N/A'}")
        elif args.sweep:
            rows = sweep_momentum(dates, panel)
            print(f"{'#':<3} {'파라미터':<26} 지표")
            for i, r in enumerate(rows[: args.top], 1):
                print(f"{i:<3} {r.label():<26} {r.is_metrics.summary()}")
            print(f"\n🏆 최적: {rows[0].label()} → {rows[0].is_metrics.summary()}")
        else:
            cfg = MomentumConfig(lookback=args.lookback, top_k=args.top_k, hold_days=args.hold)
            metrics = evaluate_config(dates, panel, cfg)
            print(f"모멘텀 {cfg.lookback}일 / 상위 {cfg.top_k}종목 / {cfg.hold_days}일 보유")
            print(f"  {metrics.summary()}")
    finally:
        await db.close()

    print(f"\n소요: {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
