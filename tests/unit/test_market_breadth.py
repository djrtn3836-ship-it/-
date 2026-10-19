"""시장 폭 계산 테스트 (2026-10-19 — 정보 커버리지 공백 보완)."""
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analytics.market_breadth import (  # noqa: E402
    compute_breadth,
    format_breadth,
    load_breadth_series,
)


def _series(days: int, base: float = 100.0, step: float = 1.0, end: str = "2026-10-19"):
    d0 = date.fromisoformat(end) - timedelta(days=days - 1)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range(days)]
    closes = [base + step * i for i in range(days)]
    return {"dates": dates, "close": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes]}


class TestCompute:

    def test_uptrend_all_above(self):
        series = {f"t{i}": _series(65, step=1.0) for i in range(30)}
        b = compute_breadth(series)
        assert b["n"] == 30
        assert b["above_ma20_pct"] == 100.0 and b["above_ma60_pct"] == 100.0
        assert b["advancers"] == 30

    def test_downtrend_all_below(self):
        series = {f"t{i}": _series(65, step=-1.0) for i in range(30)}
        b = compute_breadth(series)
        assert b["above_ma20_pct"] == 0.0 and b["decliners"] == 30

    def test_new_high_low(self):
        up = _series(25, step=1.0)  # 매일 신고
        down = _series(25, step=-1.0)  # 매일 신저
        b = compute_breadth({"a": up, "b": down})
        assert b["new_highs"] == 1 and b["new_lows"] == 1

    def test_stale_series_excluded(self):
        fresh = _series(65, end="2026-10-19")
        old = _series(65, end="2026-10-01")  # 7일+ 경과 → 제외
        b = compute_breadth({"a": fresh, "b": old})
        assert b["n"] == 1

    def test_empty(self):
        assert compute_breadth({})["n"] == 0

    def test_short_history_counts_for_breakout_only(self):
        b = compute_breadth({"a": _series(10)})  # MA20/60 불가
        assert b["n"] == 1
        assert b["above_ma20_pct"] is None
        assert b["new_highs"] == 0 and b["breakout_n"] == 0


class TestFormat:

    def test_format_line(self):
        b = compute_breadth({f"t{i}": _series(65) for i in range(25)})
        line = format_breadth(b)
        assert line and "MA20↑" in line and "상승/하락" in line

    def test_small_sample_hidden(self):
        assert format_breadth(compute_breadth({f"t{i}": _series(65) for i in range(5)})) is None


class TestLoader:

    def test_load_from_sqlite(self, tmp_path):
        db = tmp_path / "t.db"
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE ohlcv (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)"
        )
        d0 = date.today() - timedelta(days=30)
        for i in range(30):
            d = (d0 + timedelta(days=i)).isoformat()
            con.execute("INSERT INTO ohlcv VALUES ('005930',?,100,101,99,100,10)", (d,))
            con.execute("INSERT INTO ohlcv VALUES ('000660',?,50,51,49,50,10)", (d,))
        con.execute("INSERT INTO ohlcv VALUES ('^KS11',?,1,1,1,1,0)", (d0.isoformat(),))
        con.commit()
        con.close()
        series = load_breadth_series(db, lookback_days=90)
        assert set(series) == {"005930", "000660"}, "지수 티커(^) 제외"
        assert len(series["005930"]["close"]) == 30


class TestWiring:

    def test_briefing_uses_breadth(self):
        src = (Path(__file__).resolve().parents[2] / "report" / "briefing.py").read_text(encoding="utf-8")
        assert "compute_breadth" in src and "format_breadth" in src
