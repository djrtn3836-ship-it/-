# -*- coding: utf-8 -*-
"""신호 기록부(S138) 테스트."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analytics.market_breadth import S  # noqa: E402
from scheduler import signal_logger as sl  # noqa: E402


def _ctx(**over):
    base = {"breadth": 12.0, "kospi5d": -2.0, "nasdaq5d": 1.0, "gap5": -3.0, "vol_ratio": 1.0}
    base.update(over)
    return base


class TestActive:

    def test_panic_combo(self):
        keys = {a["signal"] for a in sl.active_signals(_ctx())}
        assert "breadth_le15" in keys and "breadth_le15_down" in keys and "breadth_le20" in keys
        assert "breadth_le10" not in keys and "kospi5_le-5" not in keys

    def test_gap_condition(self):
        on = {a["signal"] for a in sl.active_signals(_ctx(gap5=-6.2))}
        off = {a["signal"] for a in sl.active_signals(_ctx(gap5=-1.0))}
        assert "kospi_nasdaq_gap_le-5p" in on
        assert "kospi_nasdaq_gap_le-5p" not in off

    def test_none_safe(self):
        assert sl.active_signals({}) == [], "데이터 없음 → 신호 없음(추정 금지)"


class TestLog:

    def test_log_and_dedupe(self, tmp_path):
        log = tmp_path / "log.jsonl"
        r1 = sl.log_today(log_path=log, ctx=_ctx(), today="2026-10-19")
        r2 = sl.log_today(log_path=log, ctx=_ctx(), today="2026-10-19")
        assert r1["logged"] > 0 and r2["logged"] == 0, "같은 날 중복 기록 금지"
        rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == r1["logged"] and all(r["date"] == "2026-10-19" for r in rows)

    def test_next_day_logs_again(self, tmp_path):
        log = tmp_path / "log.jsonl"
        sl.log_today(log_path=log, ctx=_ctx(), today="2026-10-19")
        r = sl.log_today(log_path=log, ctx=_ctx(), today="2026-10-20")
        assert r["logged"] > 0


def _index(n=40):
    dates = [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]
    return S(dates, [100.0 + i for i in range(n)], [], [])


class TestSettle:

    def test_settle_horizon_and_idempotent(self, tmp_path):
        idx = _index()
        log = tmp_path / "log.jsonl"
        settled = tmp_path / "set.jsonl"
        log.write_text(
            json.dumps({"date": idx.dates[0], "signal": "breadth_le15", "grade": "A", "label": "x"}) + "\n",
            encoding="utf-8",
        )
        r = sl.settle_all(log_path=log, settled_path=settled, horizon=5, index=idx)
        assert r["settled"] == 1
        row = json.loads(settled.read_text(encoding="utf-8").splitlines()[0])
        exp = (idx.close[5] / idx.close[0] - 1.0) * 100.0
        assert abs(row["fwd_pct"] - round(exp, 3)) < 1e-9 and row["hit"] is True
        assert sl.settle_all(log_path=log, settled_path=settled, horizon=5, index=idx)["settled"] == 0

    def test_young_pending(self, tmp_path):
        idx = _index()
        log = tmp_path / "log.jsonl"
        settled = tmp_path / "set.jsonl"
        log.write_text(
            json.dumps({"date": idx.dates[-1], "signal": "x", "grade": "C", "label": "x"}) + "\n",
            encoding="utf-8",
        )
        r = sl.settle_all(log_path=log, settled_path=settled, horizon=5, index=idx)
        assert r["settled"] == 0 and r["pending"] == 1


class TestScorecard:

    def test_agg(self, tmp_path):
        p = tmp_path / "set.jsonl"
        rows = [
            {"signal": "breadth_le15", "grade": "A", "label": "폭≤15", "fwd_pct": 2.0, "hit": True},
            {"signal": "breadth_le15", "grade": "A", "label": "폭≤15", "fwd_pct": -1.0, "hit": False},
            {"signal": "vol", "grade": "C", "label": "거래량", "fwd_pct": 0.5, "hit": True},
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        sc = sl.scorecard(settled_path=p)
        top = [x for x in sc if x["signal"] == "breadth_le15"][0]
        assert top["n"] == 2 and abs(top["avg"] - 0.5) < 1e-9 and top["hit"] == 50.0
        assert "신호 기록부 성적" in sl.scorecard_text(settled_path=p)


class TestRefresh:

    def test_refresh_with_fake_db(self):
        class FakeDB:
            def __init__(self):
                self.rows = []

            async def save_ohlcv_batch(self, recs):
                self.rows += recs
                return len(recs)

            async def flush_pending(self):
                pass

            async def close(self):
                pass

        db = FakeDB()

        def fake_fetch(tk, period):
            return [{"date": "2026-10-19", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}]

        out = asyncio.run(sl.refresh_series(fetcher=fake_fetch, db=db))
        assert len(out) == len(sl.REFRESH_TICKERS)
        assert all(v == 1 for v in out.values())


class TestWiring:

    def test_bootstrap_job_and_count(self):
        src = (Path(__file__).resolve().parents[2] / "app" / "bootstrap.py").read_text(encoding="utf-8")
        assert '"signal_log"' in src
        assert 'self.startup_details["job_count"] = 30' in src

    def test_weekly_includes_registry(self):
        src = (Path(__file__).resolve().parents[2] / "scheduler" / "weekly_scorecard.py").read_text(encoding="utf-8")
        assert "scorecard_text" in src
