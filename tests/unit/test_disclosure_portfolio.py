# -*- coding: utf-8 -*-
"""공시 전략 모의 포트폴리오(S146) 테스트."""
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import analytics.disclosure_portfolio as pf  # noqa: E402
from report import html_dashboard as hd  # noqa: E402
from report.briefing import _portfolio_section  # noqa: E402


def make_days(n=30, start="2022-01-03"):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


class FakeDB:
    def __init__(self, series):
        self.series = series

    async def get_ohlcv_range(self, ticker, start, end):
        return self.series.get(ticker, [])

    async def close(self):
        pass


def _db():
    ds = make_days()
    rows = [{"date": d, "close": 100.0 + i} for i, d in enumerate(ds)]
    return FakeDB({"A": rows, "^KS11": rows}), ds


def _log(tmp_path, entries):
    p = tmp_path / "watch.jsonl"
    p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8")
    return p


class TestRun:

    def test_open_and_exit(self, tmp_path):
        db, ds = _db()
        log = _log(tmp_path, [{
            "date": "20220105", "ticker": "A", "name": "가", "category": "주식소각",
            "grade": "A_생존", "logged_at": "2022-01-05T14:50:00",
        }])
        state = tmp_path / "state.json"

        d1 = pf.run_portfolio(today="2022-01-05", state_path=state, watch_log=log, db=db, max_pos=1)
        assert d1["opened"] == 1 and d1["positions"] == 1
        assert abs(d1["equity"] - 1 / 1.0015) < 1e-4, "매수 비용 반영"

        d2 = pf.run_portfolio(today="2022-01-06", state_path=state, watch_log=log, db=db, max_pos=1)
        assert d2["sold"] == 1 and d2["positions"] == 0
        expected = (103 / 102) * (1 / 1.0015) * 0.9985
        assert abs(d2["equity"] - expected) < 1e-4
        assert d2["closed"] == 1 and d2["win_rate"] == 100.0

    def test_idempotent_same_day(self, tmp_path):
        db, _ = _db()
        log = _log(tmp_path, [{
            "date": "20220105", "ticker": "A", "category": "자사주매입", "grade": "A_생존",
        }])
        state = tmp_path / "state.json"
        pf.run_portfolio(today="2022-01-05", state_path=state, watch_log=log, db=db)
        again = pf.run_portfolio(today="2022-01-05", state_path=state, watch_log=log, db=db)
        assert again["opened"] == 0 and again["positions"] == 1, "같은 날 중복 진입 금지"

    def test_hold_five_days(self, tmp_path):
        db, _ = _db()
        log = _log(tmp_path, [{
            "date": "20220105", "ticker": "A", "category": "자사주매입", "grade": "A_생존",
        }])
        state = tmp_path / "state.json"
        pf.run_portfolio(today="2022-01-05", state_path=state, watch_log=log, db=db)
        out = pf.run_portfolio(today="2022-01-06", state_path=state, watch_log=log, db=db)
        assert out["positions"] == 1, "5일 보유 중"
        out = pf.run_portfolio(today="2022-01-12", state_path=state, watch_log=log, db=db)
        assert out["positions"] == 0 and out["closed"] == 1

    def test_after_close_scan_excluded(self, tmp_path):
        db, _ = _db()
        log = _log(tmp_path, [{
            "date": "20220105", "ticker": "A", "category": "자사주매입", "grade": "A_생존",
            "logged_at": "2022-01-05T16:40:00",
        }])
        state = tmp_path / "state.json"
        out = pf.run_portfolio(today="2022-01-05", state_path=state, watch_log=log, db=db)
        assert out["opened"] == 0, "종가 후 공시는 당일 진입 불가"

    def test_max_positions(self, tmp_path):
        db, ds = _db()
        rows = [{"date": d, "close": 100.0 + i} for i, d in enumerate(ds)]
        db.series["B"] = rows
        log = _log(tmp_path, [
            {"date": "20220105", "ticker": t, "category": "자사주매입", "grade": "A_생존"}
            for t in ("A", "B", "A")
        ])
        state = tmp_path / "state.json"
        out = pf.run_portfolio(today="2022-01-05", state_path=state, watch_log=log, db=db, max_pos=1)
        assert out["positions"] == 1


class TestSummary:

    def test_load_summary(self, tmp_path):
        db, _ = _db()
        log = _log(tmp_path, [{"date": "20220105", "ticker": "A", "category": "주식소각", "grade": "A_생존"}])
        state = tmp_path / "state.json"
        pf.run_portfolio(today="2022-01-05", state_path=state, watch_log=log, db=db)
        s = pf.load_summary(state_path=state)
        assert s["start"] == "2022-01-05" and s["positions"] == 1 and s["closed"] == 0

    def test_empty(self, tmp_path):
        assert pf.load_summary(state_path=tmp_path / "none.json") == {}


class TestDisplay:

    def test_briefing(self, monkeypatch):
        monkeypatch.setattr(pf, "load_summary", lambda **kw: {
            "start": "2026-10-20", "total_pct": 1.23, "positions": 2, "closed": 3, "win_rate": 66.7,
        })
        lines = _portfolio_section()
        assert any("📊" in x for x in lines) and any("+1.23%" in x for x in lines)

    def test_dashboard(self):
        html = hd.render_html({"signals": {
            "date": "2026-10-20", "active": [], "scorecard": [],
            "disclosures": {"date": "20261020", "rows": [], "settled": [],
                            "portfolio": {"total_pct": 1.23, "positions": 2, "closed": 3, "win_rate": 66.7}},
        }})
        assert "공시 전략 모의(가상)" in html and "1.23%" in html

class TestDedupe20Days:

    def test_same_category_within_20d_skipped(self, tmp_path):
        db, ds = _db()
        log = _log(tmp_path, [
            {"date": "20220105", "ticker": "A", "category": "주식소각", "grade": "A_생존"},
            {"date": "20220107", "ticker": "A", "category": "주식소각", "grade": "A_생존"},
        ])
        state = tmp_path / "state.json"
        pf.run_portfolio(today="2022-01-05", state_path=state, watch_log=log, db=db)
        # 2일 후 청산되지만 같은 유형 재진입은 20거래일 내 금지
        out1 = pf.run_portfolio(today="2022-01-06", state_path=state, watch_log=log, db=db)
        assert out1["positions"] == 0, "1일 보유 청산"
        out2 = pf.run_portfolio(today="2022-01-07", state_path=state, watch_log=log, db=db)
        assert out2["opened"] == 0, "20거래일 내 동일 유형 재진입 금지"

class TestInverseVolSizing:

    def test_high_vol_gets_smaller(self, tmp_path):
        ds = make_days()
        low = [{"date": d, "close": 100.0 * (1.001 ** i)} for i, d in enumerate(ds)]
        high = [{"date": d, "close": 100.0 * (1.05 if i % 2 else 0.95)} for i, d in enumerate(ds)]
        db = FakeDB({"LOW": low, "HIGH": high, "L": low, "H": high})
        log = _log(tmp_path, [
            {"date": "20220105", "ticker": "L", "category": "주식소각", "grade": "A_생존"},
            {"date": "20220105", "ticker": "H", "category": "주식소각", "grade": "A_생존"},
        ])
        state = tmp_path / "state.json"
        out = pf.run_portfolio(today="2022-01-05", state_path=state, watch_log=log, db=db,
                               max_pos=2, sizing="inverse_vol")
        assert out["positions"] == 2
        st = json.loads(state.read_text(encoding="utf-8"))
        by = {p["ticker"]: p for p in st["positions"]}
        assert by["H"]["scale"] == 0.5 and by["L"]["scale"] == 1.5, "고변동 축소·저변동 확대"
        assert by["H"]["shares"] * by["H"]["entry_price"] < by["L"]["shares"] * by["L"]["entry_price"]

    def test_scheduler_uses_invvol(self):
        src = (Path(__file__).resolve().parents[2] / "analytics" / "disclosure_portfolio.py").read_text(encoding="utf-8")
        assert 'run_portfolio(sizing="inverse_vol"' in src
