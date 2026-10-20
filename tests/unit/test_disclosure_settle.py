# -*- coding: utf-8 -*-
"""공시 신호 자동 채점(S144) 테스트."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scheduler.disclosure_settle import (  # noqa: E402
    disclosure_stats,
    format_stats,
    settle,
    settle_one,
)


def make_days(n=60, start="2022-01-03"):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


class FakeDB:
    def __init__(self, series):
        self.series = series

    async def get_ohlcv_range(self, ticker, start, end):
        return self.series.get(ticker, [])

    async def close(self):
        pass


class TestSettleOne:

    def test_excess_math(self):
        days = make_days()
        closes = [100.0 + i for i in range(60)]
        rec = settle_one(
            {"date": "20220107", "ticker": "A", "category": "자사주매입", "grade": "A_생존"},
            days, closes, days, [100.0] * 60, horizon=5,
        )
        assert rec["entry_date"] == "2022-01-07"
        assert rec["ret1"] == pytest.approx(105 / 104 - 1, abs=1e-5)
        assert rec["excess1"] == pytest.approx(105 / 104 - 1, abs=1e-5)
        assert rec["ret5"] == pytest.approx(109 / 104 - 1, abs=1e-5)

    def test_kospi_subtracted(self):
        days = make_days()
        closes = [100.0] * 60
        k_closes = [100.0 + 0.5 * i for i in range(60)]
        rec = settle_one({"date": "20220107", "ticker": "A", "grade": "A_생존"},
                         days, closes, days, k_closes, horizon=5)
        assert rec["excess5"] == pytest.approx(-(104.5 / 102 - 1), abs=1e-5)

    def test_insufficient_pending(self):
        days = make_days()
        assert settle_one({"date": "20220301", "ticker": "A"}, days, [100.0] * 60, days, [100.0] * 60) is None
        assert settle_one({"date": "bad"}, days, [100.0] * 60, days, [100.0] * 60) is None

    def test_weekend_event_rolls_to_next_day(self):
        days = ["2022-01-03", "2022-01-04"] + make_days(60)
        days = sorted(set(days))
        closes = [100.0 + i for i in range(len(days))]
        rec = settle_one({"date": "20220101", "ticker": "A", "grade": "A_생존"},
                         days, closes, days, [100.0] * len(days), horizon=5)
        assert rec is not None and rec["entry_date"] == "2022-01-03"


class TestSettle:

    def _db(self):
        days = make_days()
        series = {
            "A": [{"date": d, "close": 100.0 + i} for i, d in enumerate(days)],
            "^KS11": [{"date": d, "close": 100.0} for d in days],
        }
        return FakeDB(series), days

    def test_writes_and_idempotent(self, tmp_path):
        db, days = self._db()
        log = tmp_path / "watch.jsonl"
        settled = tmp_path / "settled.jsonl"
        rows = [
            {"date": "20220107", "ticker": "A", "name": "가", "category": "자사주매입", "grade": "A_생존"},
            {"date": "20220110", "ticker": "A", "name": "가", "category": "소송", "grade": "B_경계"},
            {"date": "20220111", "ticker": "A", "name": "가", "category": "배당", "grade": "C_참고"},
        ]
        log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

        out1 = settle(log_path=log, settled_path=settled, db=db)
        assert out1["settled"] == 2, "A/B만 채점, C 제외"
        assert settled.read_text(encoding="utf-8").count("\n") == 2

        out2 = settle(log_path=log, settled_path=settled, db=db)
        assert out2["settled"] == 0 and out2["total_settled"] == 2, "멱등"


class TestStats:

    def test_grouping(self, tmp_path):
        p = tmp_path / "s.jsonl"
        rows = [
            {"category": "자사주매입", "grade": "A_생존", "excess5": 0.02},
            {"category": "자사주매입", "grade": "A_생존", "excess5": -0.01},
            {"category": "소송", "grade": "B_경계", "excess5": -0.03},
        ]
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        stats = disclosure_stats(settled_path=p)
        by = {(s["category"]): s for s in stats}
        assert by["자사주매입"]["n"] == 2 and by["자사주매입"]["mean5"] == 0.5
        assert by["자사주매입"]["hit5"] == 50.0
        assert by["소송"]["mean5"] == -3.0
        assert "자사주매입" in format_stats(stats)

class TestOnDemandPrices:
    """S147: 유니버스 밖 종목(일봉 미수집) 채점 — 온디맨드 시세."""

    def test_backfill_when_missing(self, monkeypatch, tmp_path):
        import scheduler.ohlcv_backfill as ob

        ds = make_days()
        rows = [{"date": d, "close": 100.0 + i} for i, d in enumerate(ds)]

        class FakeBackfiller:
            def __init__(self, delay=0.0):
                pass

            def fetch_history(self, ticker, period="3mo"):
                return (rows, ".KQ")

        monkeypatch.setattr(ob, "OHLCVBackfiller", FakeBackfiller)

        class FDB:
            async def get_ohlcv_range(self, ticker, start, end):
                if ticker == "^KS11":
                    return [{"date": d, "close": 100.0} for d in ds]
                return []

            async def close(self):
                pass

        log = tmp_path / "w.jsonl"
        log.write_text(json.dumps({"date": "20220107", "ticker": "B", "name": "나",
                                   "category": "자사주매입", "grade": "A_생존"}, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        out = settle(log_path=log, settled_path=tmp_path / "s.jsonl", db=FDB())
        assert out["settled"] == 1, "DB에 없어도 온디맨드로 채점"

class TestBacktestVsPaper:

    def test_both(self):
        from scheduler.weekly_scorecard import format_backtest_vs_paper

        txt = format_backtest_vs_paper(
            {"variants": {"A_all": {"portfolio": {"cagr": 30.2, "sharpe": 1.3, "mdd": 28.9}}},
             "cost_roundtrip": 0.003},
            {"start": "2026-10-20", "total_pct": 1.23, "positions": 1, "closed": 2, "win_rate": 50.0},
        )
        assert "CAGR 30.2" in txt and "모의(실전" in txt and "+1.23%" in txt

    def test_missing_paper(self):
        from scheduler.weekly_scorecard import format_backtest_vs_paper

        txt = format_backtest_vs_paper({"variants": {"A_all": {"portfolio": {"cagr": 30.2}}}}, {})
        assert "첫 실행 대기" in txt

    def test_empty(self):
        from scheduler.weekly_scorecard import format_backtest_vs_paper

        assert format_backtest_vs_paper({}, {}) == ""


class TestMarketAlert:

    def test_a_only(self):
        from scheduler.signal_logger import format_market_alert

        txt = format_market_alert(["breadth_le15", "kospi5_le-5"])
        assert "🎯" in txt and "폭≤15%" in txt and "KOSPI 5일≤-5%" not in txt

    def test_no_a(self):
        from scheduler.signal_logger import format_market_alert

        assert format_market_alert(["kospi5_le-5"]) is None
        assert format_market_alert([]) is None

class TestAfterCloseExcluded:

    def test_after_close_not_settled(self, tmp_path):
        days = make_days()
        series = {"A": [{"date": d, "close": 100.0 + i} for i, d in enumerate(days)],
                  "^KS11": [{"date": d, "close": 100.0} for d in days]}
        log = tmp_path / "w.jsonl"
        log.write_text(json.dumps({"date": "20220107", "ticker": "A", "grade": "A_생존",
                                   "logged_at": "2022-01-07T16:40:00"}, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        out = settle(log_path=log, settled_path=tmp_path / "s.jsonl", db=FakeDB(series))
        assert out["settled"] == 0, "종가 후 공시는 매매 불가 → 채점 제외"
