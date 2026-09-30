# -*- coding: utf-8 -*-
"""tests/unit/test_backtest_runner.py - 백테스터 배선(DB OHLCV → 시뮬레이션) 검증.

검증 항목:
    TestIndicators      : sma / ema / rsi 순수 구현 정확성
    TestMACrossSimulator: 진입 신호, 보유기간 청산, 익절, 손절
    TestBacktestRunner  : DB 연동(Walk-Forward), 빈 데이터 안전성, 실 DB 저장→조회
"""

import math
from datetime import date, timedelta
from typing import Any, Dict, List

import pytest

from data.db_manager import DatabaseManager
from validation.backtest_runner import (
    BacktestRunner,
    MACrossSimulator,
    SimConfig,
    ema,
    rsi,
    sma,
)


def _bars(closes: List[float], highs: List[float] | None = None, lows: List[float] | None = None) -> List[Dict[str, Any]]:
    base = date(2026, 1, 1)
    out: List[Dict[str, Any]] = []
    for i, c in enumerate(closes):
        out.append(
            {
                "date": (base + timedelta(days=i)).isoformat(),
                "open": c,
                "high": highs[i] if highs else c,
                "low": lows[i] if lows else c,
                "close": c,
                "volume": 1000,
            }
        )
    return out


def _flat_then_rise() -> List[float]:
    """30봉 횡보(100) 후 10봉 상승(101~110) — 30번 인덱스에서 상향 돌파."""
    return [100.0] * 30 + [100.0 + i for i in range(1, 11)]


class _FakeDB:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def get_ohlcv_range(self, ticker: str, start: str, end: str) -> List[Dict[str, Any]]:
        return list(self.rows)


class TestIndicators:
    def test_sma(self) -> None:
        assert sma([1.0, 2.0, 3.0, 4.0, 5.0], 3) == [None, None, 2.0, 3.0, 4.0]

    def test_sma_invalid_period(self) -> None:
        with pytest.raises(ValueError):
            sma([1.0], 0)

    def test_ema_warmup_and_value(self) -> None:
        out = ema([1.0, 2.0, 3.0, 4.0], 3)
        assert out[0] is None and out[1] is None
        assert out[2] == pytest.approx(2.0)          # 첫 값은 SMA(3)
        assert out[3] == pytest.approx(3.0)          # 4*0.5 + 2*0.5

    def test_rsi_bounds_and_warmup(self) -> None:
        closes = [100.0 + math.sin(i / 3.0) for i in range(60)]
        out = rsi(closes, 14)
        assert out[13] is None                        # 워밍업
        vals = [v for v in out if v is not None]
        assert vals and all(0.0 <= v <= 100.0 for v in vals)

    def test_rsi_all_up_is_100(self) -> None:
        out = rsi([float(i) for i in range(1, 40)], 14)
        assert out[-1] == pytest.approx(100.0)


class TestMACrossSimulator:
    def test_entry_signal_index(self) -> None:
        sim = MACrossSimulator(SimConfig(short_period=5, long_period=20, hold_days=5))
        bars = _bars(_flat_then_rise())
        assert sim.entry_signals(bars) == [30]

    def test_exit_by_hold_days(self) -> None:
        sim = MACrossSimulator(SimConfig(short_period=5, long_period=20, hold_days=5))
        bars = _bars(_flat_then_rise())
        trades = sim.simulate(bars, ticker="005930")
        assert len(trades) == 1
        t = trades[0]
        assert t.entry_date == bars[30]["date"]
        assert t.exit_date == bars[35]["date"]        # 30 + hold_days
        assert t.entry_price == pytest.approx(101.0)
        assert t.return_pct == pytest.approx((106.0 - 101.0) / 101.0)

    def test_exit_by_take_profit(self) -> None:
        sim = MACrossSimulator(SimConfig(short_period=5, long_period=20, hold_days=5, take_profit_pct=0.10))
        bars = _bars(_flat_then_rise())
        bars[31]["high"] = 200.0                      # 다음 봉에서 +10% 초과
        trades = sim.simulate(bars)
        assert len(trades) == 1
        assert trades[0].exit_date == bars[31]["date"]
        assert trades[0].exit_price == pytest.approx(101.0 * 1.10)

    def test_exit_by_stop_loss(self) -> None:
        sim = MACrossSimulator(SimConfig(short_period=5, long_period=20, hold_days=5, stop_loss_pct=0.05))
        bars = _bars(_flat_then_rise())
        bars[31]["low"] = 50.0                        # 다음 봉에서 -5% 이탈
        trades = sim.simulate(bars)
        assert len(trades) == 1
        assert trades[0].exit_date == bars[31]["date"]
        assert trades[0].exit_price == pytest.approx(101.0 * 0.95)

    def test_no_signal_when_data_insufficient(self) -> None:
        sim = MACrossSimulator(SimConfig(short_period=5, long_period=20))
        assert sim.simulate(_bars([100.0] * 10)) == []


class TestBacktestRunner:
    async def test_run_with_synthetic_data(self) -> None:
        # 사인파 → 반복 교차 발생 → 폴드별 거래 생성
        closes = [100.0 + 10.0 * math.sin(i / 5.0) for i in range(220)]
        db = _FakeDB(_bars(closes))

        runner = BacktestRunner(db=db, train_ratio=0.7, min_periods=30)
        report = await runner.run("005930", "2026-01-01", "2026-12-31")

        assert report.bar_count == 220
        assert report.aggregated is not None
        assert len(report.aggregated.fold_results) >= 1
        assert report.trade_count > 0
        assert "평균 Sharpe" in report.summary_text()

    async def test_run_with_empty_data_is_safe(self) -> None:
        runner = BacktestRunner(db=_FakeDB([]))
        report = await runner.run("005930", "2026-01-01", "2026-12-31")
        assert report.bar_count == 0
        assert report.aggregated is None
        assert "OHLCV" in report.message

    async def test_run_without_db_is_safe(self) -> None:
        runner = BacktestRunner(db=None)
        report = await runner.run("005930", "2026-01-01", "2026-12-31")
        assert report.bar_count == 0

    async def test_run_reads_real_db_rows(self, tmp_path) -> None:
        db = DatabaseManager(db_path=tmp_path / "bt.db")
        await db.init_db()

        base = date(2026, 1, 1)
        for i in range(150):
            close = 10000.0 + 500.0 * math.sin(i / 4.0)
            await db.save_ohlcv(
                "005930",
                (base + timedelta(days=i)).isoformat(),
                {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1000},
            )
        await db._flush_pending()

        runner = BacktestRunner(db=db, train_ratio=0.7, min_periods=30)
        report = await runner.run("005930", "2026-01-01", "2026-12-31")

        assert report.bar_count == 150
        assert report.trade_count > 0
        await db.close()
