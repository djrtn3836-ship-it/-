"""시장 폭 계산 테스트 (2026-10-19 — 정보 커버리지 공백 보완)."""
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analytics.market_breadth import (  # noqa: E402
    PANIC_THRESHOLD,
    breadth_percentile,
    compute_breadth,
    format_breadth,
    load_breadth_history,
    load_breadth_series,
    refresh_history,
    save_history,
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


class TestHistoryAndPercentile:
    """S132: 역사 백분위(브리핑 병기) + 패닉권 라인."""

    def test_load_history_roundtrip(self, tmp_path):
        p = tmp_path / "h.json"
        p.write_text('{"dates": {"2026-10-18": 11.0, "2026-10-19": 20.0}}', encoding="utf-8")
        h = load_breadth_history(p)
        assert h == {"2026-10-18": 11.0, "2026-10-19": 20.0}

    def test_load_history_missing_or_broken(self, tmp_path):
        assert load_breadth_history(tmp_path / "nope.json") == {}
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        assert load_breadth_history(bad) == {}

    def test_percentile(self):
        h = {f"d{i}": float(i) for i in range(101)}  # 0..100
        assert abs(breadth_percentile(h, 50.0) - 51 / 101 * 100) < 1e-9, "이하 포함 카운트"
        assert breadth_percentile(h, 0.0) == 1 / 101 * 100
        assert breadth_percentile({}, 50.0) is None
        assert breadth_percentile(h, None) is None

    def test_format_with_history_shows_percentile(self):
        b = compute_breadth({f"t{i}": _series(65) for i in range(25)})
        line = format_breadth(b, {f"d{i}": float(i) for i in range(101)})
        assert line and "역사 백분위 100%" in line

    def test_format_without_history_unchanged(self):
        b = compute_breadth({f"t{i}": _series(65) for i in range(25)})
        line = format_breadth(b)
        assert line and "백분위" not in line

    def test_panic_threshold_value(self):
        assert PANIC_THRESHOLD == 15.0


class TestHistoryRefresh:
    """S133: 폭 역사 갱신(주말 유지보수) + JSON 저장."""

    def _make_db(self, tmp_path, tickers: int = 25, days: int = 130):
        db = tmp_path / "t.db"
        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE ohlcv (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)"
        )
        d0 = date(2026, 6, 1)
        for k in range(tickers):
            for i in range(days):
                d = (d0 + timedelta(days=i)).isoformat()
                con.execute(
                    "INSERT INTO ohlcv VALUES (?,?,100,101,99,?,10)",
                    (f"t{k:03d}", d, 100.0 + i),  # 꾸준 상승 → MA20 상회 100%
                )
        for i in range(days):
            d = (d0 + timedelta(days=i)).isoformat()
            con.execute("INSERT INTO ohlcv VALUES ('^KS11',?,1,1,1,1,0)", (d,))
        con.commit()
        con.close()
        return db

    def test_save_history_roundtrip(self, tmp_path):
        n = save_history({"2026-10-18": 11.0}, tmp_path / "h.json")
        assert n == 1
        assert load_breadth_history(tmp_path / "h.json") == {"2026-10-18": 11.0}

    def test_refresh_history_happy(self, tmp_path):
        db = self._make_db(tmp_path)
        out = tmp_path / "hist.json"
        n = refresh_history(db, out)
        h = load_breadth_history(out)
        assert n == len(h) == 111, "130일 − MA20 워밍업 19일"
        assert set(h.values()) == {100.0}, "전부 상승 종목 → 폭 100%"

    def test_refresh_history_too_short_raises(self, tmp_path):
        db = self._make_db(tmp_path, days=40)
        with pytest.raises(RuntimeError):
            refresh_history(db, tmp_path / "hist.json")


class TestWiring:

    def test_briefing_uses_breadth(self):
        src = (Path(__file__).resolve().parents[2] / "report" / "briefing.py").read_text(encoding="utf-8")
        assert "compute_breadth" in src and "format_breadth" in src

    def test_briefing_has_panic_line(self):
        src = (Path(__file__).resolve().parents[2] / "report" / "briefing.py").read_text(encoding="utf-8")
        assert "PANIC_NOTE" in src and "PANIC_THRESHOLD" in src and "load_breadth_history" in src
