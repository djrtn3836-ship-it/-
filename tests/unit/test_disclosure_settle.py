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
