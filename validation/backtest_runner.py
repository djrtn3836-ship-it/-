# -*- coding: utf-8 -*-
"""validation/backtest_runner.py - 백테스터 배선 (DB OHLCV → 전략 시뮬레이션 → Walk-Forward).

배경:
    `validation/backtester.py`(418줄)는 지표/분할 엔진만 있고 실제 데이터·전략과
    연결되어 있지 않은 **고아 모듈**이었다. 본 모듈이 그 배선을 담당한다.

흐름:
    1) DB `ohlcv` 테이블에서 종목/기간 데이터 로드 (data/db_manager.get_ohlcv_range)
    2) 이동평균 교차 + 손절/익절 규칙으로 거래 시뮬레이션 (결정론적, 순수 Python)
    3) Walk-Forward 엔진에 전략 콜백으로 주입 → 폴드별/집계 지표 산출

사용:
    python -m validation.backtest_runner 005930 2026-01-01 2026-09-30

주의:
    실제 수치를 얻으려면 DB에 OHLCV가 있어야 한다(scheduler/daily_collector가 채움).
    데이터가 없으면 크래시하지 않고 "데이터 없음" 리포트를 반환한다.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.logger import setup_logger
from validation.backtester import AggregatedResult, Backtester, Trade

logger = setup_logger("backtest_runner")


# ═══════════════════════════════════════════════════════════════════
#  지표 (순수 Python, 외부 의존 없음)
# ═══════════════════════════════════════════════════════════════════

def sma(values: Sequence[float], period: int) -> List[Optional[float]]:
    """단순이동평균. 워밍업 구간은 None."""
    if period <= 0:
        raise ValueError("period must be >= 1")
    out: List[Optional[float]] = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        out.append(running / period if i + 1 >= period else None)
    return out


def ema(values: Sequence[float], period: int) -> List[Optional[float]]:
    """지수이동평균. 워밍업 구간은 None."""
    if period <= 0:
        raise ValueError("period must be >= 1")
    out: List[Optional[float]] = []
    k = 2.0 / (period + 1.0)
    prev: Optional[float] = None
    for i, v in enumerate(values):
        if i + 1 < period:
            out.append(None)
            continue
        if prev is None:
            prev = sum(values[i + 1 - period : i + 1]) / period
        else:
            prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(values: Sequence[float], period: int = 14) -> List[Optional[float]]:
    """Wilder RSI. 워밍업 구간은 None."""
    if period <= 0:
        raise ValueError("period must be >= 1")
    out: List[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return out

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return out


# ═══════════════════════════════════════════════════════════════════
#  시뮬레이터
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SimConfig:
    """시뮬레이션 규칙."""

    short_period: int = 5
    long_period: int = 20
    hold_days: int = 5
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10


@dataclass
class BacktestReport:
    """백테스트 실행 결과 요약."""

    ticker: str
    start_date: str
    end_date: str
    bar_count: int
    trade_count: int
    aggregated: Optional[AggregatedResult] = None
    message: str = ""
    strategy: str = "ma_cross"

    def summary_text(self) -> str:
        head = (
            f"📈 백테스트 [{self.ticker}] {self.start_date} ~ {self.end_date} (전략: {self.strategy})\n"
            f"  봉 수: {self.bar_count}  거래 수: {self.trade_count}"
        )
        if self.aggregated is None or not self.aggregated.fold_results:
            return head + f"\n  ⚠️ {self.message or '결과 없음'}"
        return head + "\n" + self.aggregated.summary_text()


def simulate_entries(
    bars: Sequence[Dict[str, Any]],
    entry_indices: Sequence[int],
    config: Optional["SimConfig"] = None,
    ticker: str = "UNKNOWN",
) -> List[Trade]:
    """진입 인덱스 목록에 대해 손절/익절/보유기간 규칙으로 거래를 생성한다.

    포지션은 겹치지 않는다(청산 이후부터 다음 진입 탐색).
    MA교차 시뮬레이터와 앙상블(프로덕션 전략) 시뮬레이터가 공유한다.

    Args:
        bars: 날짜 오름차순 OHLCV 봉 목록
        entry_indices: 진입할 봉의 인덱스(오름차순)
        config: 청산 규칙(미지정 시 기본 SimConfig)
        ticker: 거래 기록에 남길 종목 코드
    """
    cfg = config or SimConfig()
    closes = [float(b.get("close", 0.0) or 0.0) for b in bars]
    highs = [float(b.get("high", 0.0) or 0.0) for b in bars]
    lows = [float(b.get("low", 0.0) or 0.0) for b in bars]
    dates = [str(b.get("date", "")) for b in bars]

    trades: List[Trade] = []
    signal_set = set(entry_indices)
    i = 0

    while i < len(bars):
        if i not in signal_set:
            i += 1
            continue

        entry_price = closes[i]
        if entry_price <= 0:
            i += 1
            continue

        stop_price = entry_price * (1.0 - cfg.stop_loss_pct)
        tp_price = entry_price * (1.0 + cfg.take_profit_pct)
        last_idx = min(i + cfg.hold_days, len(bars) - 1)

        exit_idx = last_idx
        exit_price = closes[last_idx]
        for j in range(i + 1, last_idx + 1):
            if lows[j] > 0 and lows[j] <= stop_price:
                exit_idx, exit_price = j, stop_price
                break
            if highs[j] > 0 and highs[j] >= tp_price:
                exit_idx, exit_price = j, tp_price
                break

        trades.append(
            Trade(
                ticker=ticker,
                entry_date=dates[i],
                exit_date=dates[exit_idx],
                entry_price=entry_price,
                exit_price=exit_price,
                action="BUY",
                return_pct=(exit_price - entry_price) / entry_price,
            )
        )
        i = exit_idx + 1

    return trades


class MACrossSimulator:
    """단기/장기 이동평균 교차 + 손절/익절 시뮬레이터.

    규칙:
        진입: short MA가 long MA를 상향 돌파한 봉의 종가
        청산: -손절% 도달 → 손절가, +익절% 도달 → 익절가,
              그 외 보유기간(hold_days) 경과 시 종가
        포지션은 겹치지 않는다(청산 이후부터 다음 진입 탐색).
    """

    def __init__(self, config: Optional[SimConfig] = None) -> None:
        self.cfg = config or SimConfig()

    @property
    def warmup(self) -> int:
        """지표 계산에 필요한 최소 봉 수."""
        return max(self.cfg.long_period, self.cfg.short_period)

    def entry_signals(self, bars: Sequence[Dict[str, Any]]) -> List[int]:
        """상향 돌파가 발생한 인덱스 목록."""
        closes = [float(b.get("close", 0.0) or 0.0) for b in bars]
        if len(closes) < self.warmup + 1:
            return []
        s = sma(closes, self.cfg.short_period)
        l = sma(closes, self.cfg.long_period)

        signals: List[int] = []
        for i in range(1, len(closes)):
            s_now, l_now = s[i], l[i]
            s_prev, l_prev = s[i - 1], l[i - 1]
            if None in (s_now, l_now, s_prev, l_prev):
                continue
            assert s_now is not None and l_now is not None
            assert s_prev is not None and l_prev is not None
            if s_prev <= l_prev and s_now > l_now:
                signals.append(i)
        return signals

    def simulate(self, bars: Sequence[Dict[str, Any]], ticker: str = "UNKNOWN") -> List[Trade]:
        """봉 시퀀스에 대해 거래 목록을 생성한다."""
        return simulate_entries(bars, self.entry_signals(bars), self.cfg, ticker)


# ═══════════════════════════════════════════════════════════════════
#  러너 (DB → 시뮬레이션 → Walk-Forward)
# ═══════════════════════════════════════════════════════════════════

class BacktestRunner:
    """DB OHLCV를 읽어 Walk-Forward 백테스트를 수행한다.

    signal_mode:
        "ma_cross" : 기본 MA교차 데모 규칙
        "ensemble" : 프로덕션 앙상블(Trend/Reversal/Breakout) 신호
    """

    def __init__(
        self,
        db: Any = None,
        simulator: Optional[MACrossSimulator] = None,
        train_ratio: float = 0.7,
        min_periods: int = 30,
        signal_mode: str = "ma_cross",
        signal_threshold: float = 0.25,
    ) -> None:
        if signal_mode not in ("ma_cross", "ensemble"):
            raise ValueError(f"signal_mode must be 'ma_cross' or 'ensemble', got {signal_mode!r}")
        self.db = db
        self.simulator = simulator or MACrossSimulator()
        self.backtester = Backtester(train_ratio=train_ratio, min_periods=min_periods)
        self.signal_mode = signal_mode
        self.signal_threshold = signal_threshold

    async def load_ohlcv(self, ticker: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """DB에서 OHLCV 로드(날짜 오름차순). DB 미주입 시 빈 목록."""
        if self.db is None:
            return []
        rows = await self.db.get_ohlcv_range(ticker, start_date, end_date)
        return [r for r in rows if float(r.get("close", 0.0) or 0.0) > 0]

    async def run(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        mode: str = "rolling",
    ) -> BacktestReport:
        bars = await self.load_ohlcv(ticker, start_date, end_date)
        if not bars:
            return BacktestReport(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
                bar_count=0,
                trade_count=0,
                message="DB에 OHLCV 데이터가 없습니다 (daily_collector 미실행 또는 기간 불일치)",
            )

        dates = [str(b.get("date", "")) for b in bars]
        date_to_idx = {d: i for i, d in enumerate(dates)}

        if self.signal_mode == "ensemble":
            from validation.strategy_backtest import EnsembleEvaluator, _edge_indices

            evaluator = EnsembleEvaluator(net_threshold=self.signal_threshold)
            global_actions = await evaluator.signal_actions(bars)
            sim_config = self.simulator.cfg

            def strategy_fn(_train_dates: List[str], test_dates: List[str]) -> List[Trade]:
                if not test_dates:
                    return []
                first = date_to_idx.get(test_dates[0])
                last = date_to_idx.get(test_dates[-1])
                if first is None or last is None:
                    return []
                window = bars[first : last + 1]
                # 구간 시작 상태를 새 출발로 보고 로컬 엣지 계산
                local_entries = _edge_indices(global_actions[first : last + 1], offset=0)
                return simulate_entries(window, local_entries, sim_config, ticker=ticker)

            strategy_name = "ensemble"
        else:
            warmup = self.simulator.warmup
            sim_config = self.simulator.cfg

            def strategy_fn(_train_dates: List[str], test_dates: List[str]) -> List[Trade]:
                if not test_dates:
                    return []
                first = date_to_idx.get(test_dates[0])
                last = date_to_idx.get(test_dates[-1])
                if first is None or last is None:
                    return []
                window = bars[max(0, first - warmup) : last + 1]
                trades = self.simulator.simulate(window, ticker=ticker)
                test_set = set(test_dates)
                return [t for t in trades if t.entry_date in test_set]

            strategy_name = "ma_cross"

        aggregated = self.backtester.run_walk_forward(ticker, dates, strategy_fn, mode=mode)
        trade_count = sum(r.total_trades for r in aggregated.fold_results)

        message = ""
        if not aggregated.fold_results:
            message = f"폴드 생성 불가 (봉 {len(bars)}개, min_periods={self.backtester._engine._min_periods})"
        elif trade_count == 0:
            message = "폴드는 생성됐으나 진입 신호가 없었습니다"

        return BacktestReport(
            ticker=ticker,
            start_date=dates[0],
            end_date=dates[-1],
            bar_count=len(bars),
            trade_count=trade_count,
            aggregated=aggregated,
            message=message,
            strategy=strategy_name,
        )


async def _main(argv: Sequence[str]) -> int:
    import argparse
    import sys

    # Windows 콘솔(cp949)에서 이모지 출력 시 크래시 방지
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="Walk-Forward 백테스트 (DB OHLCV)")
    parser.add_argument("ticker", help="종목코드 (예: 005930)")
    parser.add_argument("start_date", help="시작일 YYYY-MM-DD")
    parser.add_argument("end_date", help="종료일 YYYY-MM-DD")
    parser.add_argument(
        "--strategy",
        choices=["ma_cross", "ensemble"],
        default="ma_cross",
        help="ma_cross(기본 데모) | ensemble(프로덕션 3전략 앙상블)",
    )
    parser.add_argument("--compare", action="store_true", help="두 전략을 모두 실행해 비교")
    args = parser.parse_args(list(argv[1:]))

    from data.db_manager import DatabaseManager

    db = DatabaseManager()
    await db.init_db()
    try:
        modes = ["ma_cross", "ensemble"] if args.compare else [args.strategy]
        for m in modes:
            runner = BacktestRunner(db=db, signal_mode=m)
            report = await runner.run(args.ticker, args.start_date, args.end_date)
            print(report.summary_text())
            print("-" * 50)
    finally:
        await db.close()
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(asyncio.run(_main(sys.argv)))
