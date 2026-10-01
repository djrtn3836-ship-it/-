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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.logger import setup_logger

logger = setup_logger("momentum_backtest")

TRADING_DAYS_PER_YEAR = 252


@dataclass
class MomentumConfig:
    lookback: int = 60
    top_k: int = 10
    hold_days: int = 20
    # 리밸런싱 1회당 왕복 거래비용(수수료+세금+슬리피지 근사). 0.0=비용 무시(순수 로직)
    cost_pct: float = 0.0
    # ── 생존편향 스트레스 (P2-13) ───────────────────────────────
    # 유니버스가 '현재 상장 종목' 기준이라 상장폐지 종목이 빠져 수익률이 과대평가된다.
    # delist_rate_annual: 연간 상장폐지율 가정 (0.0=스트레스 미적용)
    # delist_loss: 상폐 시 손실률 (0.6=원금 60% 손실)
    delist_rate_annual: float = 0.0
    delist_loss: float = 0.6
    # ── 횡단면 랭킹 방식 (P5 신규 전략 발굴) ────────────────────
    # "momentum"(기본) | "reversal"(단기반전) | "low_vol"(저변동성) | "high_52w"(52주고가 근접)
    rank_mode: str = "momentum"


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


def rank_scores(
    panel: Sequence[Dict[str, float]],
    i: int,
    config: MomentumConfig,
) -> Dict[str, float]:
    """리밸런싱 시점 i에서 종목별 랭킹 점수(높을수록 매수 우선).

    `rank_mode`에 따라 다른 횡단면 팩터를 계산한다. 모두 롱온리 전략이며
    시장(동일가중 유니버스) 대비 초과수익(알파)이 있는지로 평가한다.
    """
    cur_row = panel[i]
    mode = config.rank_mode

    if mode == "reversal":
        # 단기(20거래일) 반전: 최근 많이 빠진 종목 매수
        lb = min(20, max(1, config.lookback))
        if i - lb < 0:
            return {}
        base_row = panel[i - lb]
        return {
            t: -(p / base_row[t] - 1.0)
            for t, p in cur_row.items()
            if base_row.get(t) and base_row[t] > 0
        }

    if mode == "low_vol":
        # 저변동성: lookback 구간 일간수익률 표준편차가 작은 종목
        lb = config.lookback
        start = max(0, i - lb)
        scores: Dict[str, float] = {}
        for t in cur_row:
            rets: List[float] = []
            prev: Optional[float] = None
            for row in panel[start : i + 1]:
                px = row.get(t)
                if prev is not None and prev > 0 and px:
                    rets.append(px / prev - 1.0)
                prev = px
            if len(rets) >= 10:
                scores[t] = -statistics.pstdev(rets)
        return scores

    if mode == "high_52w":
        # 52주 고가 근접도(모멘텀 변형): 현재가 / 52주 최고가
        lb = min(252, i)
        start = max(0, i - lb)
        window = panel[start : i + 1]
        scores = {}
        for t in cur_row:
            hist = [row[t] for row in window if t in row]
            if len(hist) >= 60:
                peak = max(hist)
                if peak > 0:
                    scores[t] = cur_row[t] / peak
        return scores

    # 기본: lookback 수익률(횡단면 모멘텀)
    base_row = panel[i - config.lookback]
    return {
        t: p / base_row[t] - 1.0
        for t, p in cur_row.items()
        if base_row.get(t) and base_row[t] > 0
    }


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
        cur_row, future_row = panel[i], panel[i + config.hold_days]

        momentum = rank_scores(panel, i, config)
        if not momentum:
            i += config.hold_days
            continue

        picks = select_top_k(momentum, config.top_k)
        period_rets: List[float] = []
        for t in picks:
            p0 = cur_row.get(t)
            if not p0 or p0 <= 0:
                continue
            p1 = future_row.get(t)
            if p1 and p1 > 0:
                period_rets.append(p1 / p0 - 1.0)
            else:
                # 보유 기간 중 가격 데이터 소멸 = 상장폐지/거래정지로 간주.
                # (과거에는 이 종목을 조용히 제외해 생존편향을 만들었다)
                period_rets.append(-abs(config.delist_loss))
                logger.debug(f"[생존편향] {t} 가격 소멸 → {config.delist_loss:.0%} 손실 반영")
        if period_rets:
            gross = sum(period_rets) / len(period_rets)  # 동일가중
            # 연간 상폐율을 보유기간으로 환산한 기대 손실(스트레스)
            delist_drag = (
                config.delist_rate_annual
                * (config.hold_days / TRADING_DAYS_PER_YEAR)
                * abs(config.delist_loss)
            )
            returns.append(gross - config.cost_pct - delist_drag)  # 거래비용·상폐 스트레스 차감
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
class AlphaBeta:
    """전략 수익률을 시장(동일가중 유니버스)에 회귀한 결과.

    OOS 구간이 강세장일 때 "엣지"로 보이는 것이 단순 베타(시장 상승 노출)인지
    분리하기 위해 사용한다. 알파가 유의하지 않으면 엣지가 아니라 베타다.
    """
    alpha_period: float = 0.0     # 리밸런싱 1기간 초과수익
    alpha_annual: float = 0.0     # 연환산 초과수익
    beta: float = 0.0
    r_squared: float = 0.0
    t_stat: float = 0.0           # 알파 t통계량 (|t| > 2 대략 유의)
    n: int = 0
    bench_mean: float = 0.0

    def summary(self) -> str:
        verdict = "유의(알파 있음)" if abs(self.t_stat) > 2.0 else "비유의(베타 우려)"
        return (
            f"기간 {self.n}회 | α(연) {self.alpha_annual:+.1%} | β {self.beta:.2f} | "
            f"R² {self.r_squared:.2f} | t {self.t_stat:+.2f} → {verdict}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alpha_period": round(self.alpha_period, 6),
            "alpha_annual": round(self.alpha_annual, 4),
            "beta": round(self.beta, 4),
            "r_squared": round(self.r_squared, 4),
            "t_stat": round(self.t_stat, 4),
            "n": self.n,
            "bench_mean": round(self.bench_mean, 6),
        }


def benchmark_returns(
    dates: Sequence[str],
    panel: Sequence[Dict[str, float]],
    config: MomentumConfig,
) -> List[float]:
    """동일 리밸런싱 창에서 **유니버스 전체** 동일가중 수익률(시장 대용치)."""
    n = len(dates)
    returns: List[float] = []
    i = config.lookback
    while i + config.hold_days < n:
        cur_row, future_row = panel[i], panel[i + config.hold_days]
        period_rets = [
            future_row[t] / cur_row[t] - 1.0
            for t in cur_row
            if t in future_row and cur_row[t] > 0 and future_row[t] > 0
        ]
        if period_rets:
            returns.append(sum(period_rets) / len(period_rets))
        i += config.hold_days
    return returns


def alpha_beta(
    strategy: Sequence[float],
    benchmark: Sequence[float],
    periods_per_year: float,
) -> AlphaBeta:
    """전략 수익률 ~ 시장 수익률 단순 OLS 회귀 (α, β, R², t통계량)."""
    n = min(len(strategy), len(benchmark))
    if n < 3 or periods_per_year <= 0:
        return AlphaBeta(n=n)

    x = [float(v) for v in benchmark[:n]]
    y = [float(v) for v in strategy[:n]]
    mx, my = statistics.fmean(x), statistics.fmean(y)

    sxx = sum((v - mx) ** 2 for v in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    if sxx <= 1e-15:
        return AlphaBeta(n=n, bench_mean=mx)

    beta = sxy / sxx
    alpha = my - beta * mx

    resid = [b - (alpha + beta * a) for a, b in zip(x, y)]
    dof = n - 2
    if dof > 0:
        s2 = sum(r * r for r in resid) / dof
        se_alpha = math.sqrt(max(0.0, s2 * (1.0 / n + (mx * mx) / sxx)))
    else:
        se_alpha = 0.0
    if se_alpha > 1e-12:
        t_stat = alpha / se_alpha
    else:
        # 잔차분산이 0(완벽 적합)이면 표준오차가 0 → t는 무한대.
        # 알파가 0이 아니면 '완벽히 일관된 초과수익'이므로 큰 값으로 표기한다.
        t_stat = 999.0 if abs(alpha) > 1e-12 else 0.0

    sst = sum((b - my) ** 2 for b in y)
    sse = sum(r * r for r in resid)
    r_squared = (1.0 - sse / sst) if sst > 1e-15 else 0.0

    try:
        alpha_annual = (1.0 + alpha) ** periods_per_year - 1.0
    except (OverflowError, ValueError):
        alpha_annual = 0.0

    return AlphaBeta(
        alpha_period=alpha,
        alpha_annual=max(-999.0, min(999.0, alpha_annual)),
        beta=beta,
        r_squared=max(0.0, min(1.0, r_squared)),
        t_stat=max(-999.0, min(999.0, t_stat)),
        n=n,
        bench_mean=mx,
    )


def long_short_returns(
    dates: Sequence[str],
    panel: Sequence[Dict[str, float]],
    config: MomentumConfig,
) -> List[float]:
    """롱숏(시장중립) 포트폴리오 수익률 = 상위 k 매수 − 하위 k 매도.

    롱온리 전략은 β≈1이라 시장 베타가 수익을 지배해 알파가 보이지 않는다.
    롱숏은 베타를 상쇄해 **순수 초과수익(알파)을 드러내는 진단 도구**다.

    ⚠️ 진단용: 한국 개인투자자는 공매도 제약이 있어 그대로 거래할 수 없다.
    """
    n = len(dates)
    returns: List[float] = []
    i = config.lookback
    while i + config.hold_days < n:
        cur_row, future_row = panel[i], panel[i + config.hold_days]
        scores = rank_scores(panel, i, config)
        if len(scores) < config.top_k * 2:
            i += config.hold_days
            continue

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        longs = [t for t, _ in ranked[: config.top_k]]
        shorts = [t for t, _ in ranked[-config.top_k :]]

        def _avg(picks: Sequence[str]) -> float:
            rets = [
                future_row[t] / cur_row[t] - 1.0
                for t in picks
                if t in future_row and cur_row.get(t, 0) > 0 and future_row[t] > 0
            ]
            return sum(rets) / len(rets) if rets else 0.0

        spread = _avg(longs) - _avg(shorts)
        returns.append(spread - 2.0 * config.cost_pct)  # 롱·숏 양쪽 비용
        i += config.hold_days
    return returns


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
    cost_pct: float = 0.0,
) -> List[MomentumResult]:
    """IS 구간 격자 탐색(Sharpe 내림차순)."""
    results: List[MomentumResult] = []
    for lb in lookbacks:
        for k in top_ks:
            for h in holds:
                cfg = MomentumConfig(lookback=lb, top_k=k, hold_days=h, cost_pct=cost_pct)
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
    parser.add_argument("--cost", type=float, default=0.003,
                        help="리밸런싱당 왕복 거래비용(기본 0.3%%)")
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--oos-split", type=str, default="", help="OOS 분리 기준일")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--stress", action="store_true",
                        help="생존편향 스트레스 표 (상폐율 0/2/5/10%% × 상폐손실) 출력")
    parser.add_argument("--alpha-beta", action="store_true",
                        help="시장(동일가중 유니버스) 회귀로 알파/베타 분해")
    parser.add_argument("--scan", action="store_true",
                        help="횡단면 팩터 4종(momentum/reversal/low_vol/high_52w)을 α 기준으로 비교")
    parser.add_argument("--delist-loss", type=float, default=0.6,
                        help="상장폐지 시 손실률 가정 (기본 0.6 = -60%%)")
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

        if args.scan:
            ppy = TRADING_DAYS_PER_YEAR / max(args.hold, 1)
            modes = ["momentum", "reversal", "low_vol", "high_52w"]
            label = {
                "momentum": f"모멘텀({args.lookback}d)",
                "reversal": "단기반전(20d)",
                "low_vol": f"저변동성({args.lookback}d)",
                "high_52w": "52주고가근접",
            }

            def _evaluate(scope_dates, scope_panel, scope_name):
                if len(scope_dates) < args.lookback + args.hold + 5:
                    print(f"  ({scope_name}: 데이터 부족)")
                    return
                print(f"\n[{scope_name}]")
                print(f"{'전략':<18}{'Sharpe':>8}{'CAGR':>9}{'α(연)':>9}{'β':>7}{'R²':>7}{'t':>7}{'MDD':>8}")
                bench = benchmark_returns(scope_dates, scope_panel, cfg_base)
                bm = compute_metrics(bench, args.hold)
                print(f"{'benchmark':<18}{bm.sharpe:>+8.2f}{bm.cagr:>+9.1%}{'-':>9}{'-':>7}{'-':>7}{'-':>7}{bm.max_drawdown:>8.1%}")
                for mode in modes:
                    c = MomentumConfig(
                        lookback=args.lookback, top_k=args.top_k, hold_days=args.hold,
                        cost_pct=args.cost, delist_rate_annual=0.02,
                        delist_loss=args.delist_loss, rank_mode=mode,
                    )
                    rets = momentum_returns(scope_dates, scope_panel, c)
                    if len(rets) < 5:
                        print(f"{label[mode]:<18}{'데이터 부족':>20}")
                        continue
                    m = compute_metrics(rets, args.hold)
                    ab = alpha_beta(rets, bench, ppy)
                    star = " ⭐" if (ab.t_stat > 2.0 and ab.alpha_annual > 0) else ""
                    print(
                        f"{label[mode]:<18}{m.sharpe:>+8.2f}{m.cagr:>+9.1%}"
                        f"{ab.alpha_annual:>+9.1%}{ab.beta:>7.2f}{ab.r_squared:>7.2f}"
                        f"{ab.t_stat:>+7.2f}{m.max_drawdown:>8.1%}{star}"
                    )

                # ── 롱숏(시장중립) = 베타 제거 후 순수 알파 진단 ──
                print("\n  [롱숏/시장중립 — 순수 알파 진단]")
                print(f"  {'전략':<18}{'Sharpe':>8}{'CAGR':>9}{'α(연)':>9}{'β':>7}{'t':>7}")
                for mode in modes:
                    c = MomentumConfig(
                        lookback=args.lookback, top_k=args.top_k, hold_days=args.hold,
                        cost_pct=args.cost, delist_rate_annual=0.02,
                        delist_loss=args.delist_loss, rank_mode=mode,
                    )
                    ls = long_short_returns(scope_dates, scope_panel, c)
                    if len(ls) < 5:
                        continue
                    lm = compute_metrics(ls, args.hold)
                    lab = alpha_beta(ls, bench, ppy)
                    lstar = " ⭐" if (lab.t_stat > 2.0 and lab.alpha_annual > 0) else ""
                    print(
                        f"  {label[mode]:<18}{lm.sharpe:>+8.2f}{lm.cagr:>+9.1%}"
                        f"{lab.alpha_annual:>+9.1%}{lab.beta:>7.2f}{lab.t_stat:>+7.2f}{lstar}"
                    )

            cfg_base = MomentumConfig(
                lookback=args.lookback, top_k=args.top_k, hold_days=args.hold,
                cost_pct=args.cost, delist_rate_annual=0.02, delist_loss=args.delist_loss,
            )
            print(f"전략 스캔 — 비용 {args.cost:.2%}, 상폐 스트레스 2%, "
                  f"k={args.top_k}, hold={args.hold}d, lb={args.lookback}d")
            _evaluate(dates, panel, f"전구간 {args.start}~{args.end}")
            if args.oos_split:
                split = args.oos_split
                oos_dates = [d for d in dates if d > split]
                _evaluate(oos_dates, build_price_panel(series, oos_dates), f"OOS {split}~{args.end}")
            print("\n※ ⭐ = 알파 t>2 & α>0 (통계적으로 유의한 초과수익)")
        elif args.alpha_beta:
            ppy = TRADING_DAYS_PER_YEAR / max(args.hold, 1)
            cfg = MomentumConfig(
                lookback=args.lookback, top_k=args.top_k, hold_days=args.hold,
                cost_pct=args.cost, delist_rate_annual=0.02, delist_loss=args.delist_loss,
            )
            print(f"알파/베타 분해 — lb={cfg.lookback} k={cfg.top_k} hold={cfg.hold_days}, "
                  f"비용 {cfg.cost_pct:.2%}, 상폐 스트레스 2%")

            strat = momentum_returns(dates, panel, cfg)
            bench = benchmark_returns(dates, panel, cfg)
            ab_full = alpha_beta(strat, bench, ppy)
            print(f"[전구간 {args.start}~{args.end}] {ab_full.summary()}")
            print(f"    전략   {compute_metrics(strat, cfg.hold_days).summary()}")
            print(f"    벤치마크 {compute_metrics(bench, cfg.hold_days).summary()}")

            if args.oos_split:
                split = args.oos_split
                oos_dates = [d for d in dates if d > split]
                oos_panel = build_price_panel(series, oos_dates)
                strat_oos = momentum_returns(oos_dates, oos_panel, cfg)
                bench_oos = benchmark_returns(oos_dates, oos_panel, cfg)
                ab_oos = alpha_beta(strat_oos, bench_oos, ppy)
                print(f"[OOS {split}~] {ab_oos.summary()}")
                print(f"    전략   {compute_metrics(strat_oos, cfg.hold_days).summary()}")
                print(f"    벤치마크 {compute_metrics(bench_oos, cfg.hold_days).summary()}")

                is_dates = [d for d in dates if d <= split]
                is_panel = build_price_panel(series, is_dates)
                strat_is = momentum_returns(is_dates, is_panel, cfg)
                bench_is = benchmark_returns(is_dates, is_panel, cfg)
                ab_is = alpha_beta(strat_is, bench_is, ppy)
                print(f"[IS  {args.start}~{split}] {ab_is.summary()}")
                print(f"    전략   {compute_metrics(strat_is, cfg.hold_days).summary()}")
                print(f"    벤치마크 {compute_metrics(bench_is, cfg.hold_days).summary()}")

            print("\n※ β≈1 & α 비유의 → 수익은 대부분 시장 베타(강세장)이며 엣지 아님")
        elif args.oos_split and not args.stress:
            split = args.oos_split
            is_dates = [d for d in dates if d <= split]
            oos_dates = [d for d in dates if d > split]
            is_panel = build_price_panel(series, is_dates)
            oos_panel = build_price_panel(series, oos_dates)

            print(f"OOS: IS [{args.start}~{split}] {len(is_dates)}일 → OOS [{split}~{args.end}] {len(oos_dates)}일")
            is_rows = sweep_momentum(is_dates, is_panel, cost_pct=args.cost)
            print(f"\nIS 상위 {args.top}개:")
            for i, r in enumerate(is_rows[: args.top], 1):
                print(f"  {i:>2}. {r.label():<26} {r.is_metrics.summary()}")

            print("\n상위 5개 OOS 재평가:")
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
            rows = sweep_momentum(dates, panel, cost_pct=args.cost)
            print(f"{'#':<3} {'파라미터':<26} 지표")
            for i, r in enumerate(rows[: args.top], 1):
                print(f"{i:<3} {r.label():<26} {r.is_metrics.summary()}")
            print(f"\n🏆 최적: {rows[0].label()} → {rows[0].is_metrics.summary()}")
        elif args.stress:
            print(f"생존편향 스트레스 — 상폐 손실률 {args.delist_loss:.0%}, "
                  f"거래비용 {args.cost:.2%}, 전 구간 [{args.start}~{args.end}]")
            print(f"{'연간 상폐율':<12} {'총수익':>10} {'CAGR':>9} {'Sharpe':>9} {'MDD':>8} {'승률':>7}")
            for rate in (0.0, 0.02, 0.05, 0.10):
                cfg = MomentumConfig(
                    lookback=args.lookback, top_k=args.top_k, hold_days=args.hold,
                    cost_pct=args.cost, delist_rate_annual=rate,
                    delist_loss=args.delist_loss,
                )
                m = evaluate_config(dates, panel, cfg)
                print(
                    f"{rate:>10.0%}  {m.total_return:>+10.1%} {m.cagr:>+9.1%} "
                    f"{m.sharpe:>+9.2f} {m.max_drawdown:>8.1%} {m.win_rate:>7.1%}"
                )
            if args.oos_split:
                split = args.oos_split
                oos_dates = [d for d in dates if d > split]
                oos_panel = build_price_panel(series, oos_dates)
                print(f"\n[OOS {split}~{args.end}] 상폐율별")
                for rate in (0.0, 0.05, 0.10):
                    cfg = MomentumConfig(
                        lookback=args.lookback, top_k=args.top_k, hold_days=args.hold,
                        cost_pct=args.cost, delist_rate_annual=rate,
                        delist_loss=args.delist_loss,
                    )
                    m = evaluate_config(oos_dates, oos_panel, cfg)
                    print(f"{rate:>10.0%}  {m.total_return:>+10.1%} {m.cagr:>+9.1%} "
                          f"{m.sharpe:>+9.2f} {m.max_drawdown:>8.1%} {m.win_rate:>7.1%}")
        else:
            cfg = MomentumConfig(
                lookback=args.lookback, top_k=args.top_k, hold_days=args.hold, cost_pct=args.cost
            )
            metrics = evaluate_config(dates, panel, cfg)
            print(f"모멘텀 {cfg.lookback}일 / 상위 {cfg.top_k}종목 / {cfg.hold_days}일 보유")
            print(f"  {metrics.summary()}")
    finally:
        await db.close()

    print(f"\n소요: {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
