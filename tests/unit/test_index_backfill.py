"""지수 시계열 백필 테스트 (2026-10-16 — 벤치마크 정확화)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scheduler.index_backfill import backfill_indices  # noqa: E402


class FakeDB:
    def __init__(self):
        self.saved = []

    async def save_ohlcv_batch(self, records):
        self.saved.extend(records)
        return len(records)


def _fake_fetch(n_rows):
    def _f(yf_ticker, period):
        if yf_ticker == "^KS200":
            return [{"date": "2026-10-16", "close": 350.0}]  # 1행 → 저장 생략 시나리오
        return [
            {
                "date": f"2026-10-{d:02d}", "open": 100.0 + d, "high": 102.0 + d,
                "low": 99.0 + d, "close": 101.0 + d, "volume": 1000 * d,
            }
            for d in range(1, n_rows + 1)
        ]

    return _f


class TestIndexBackfill:

    def test_saves_with_kospi200_proxy(self):
        db = FakeDB()
        res = asyncio.run(backfill_indices(db, fetcher=_fake_fetch(70), min_rows=60))
        assert res["KOSPI"] == 70 and res["KOSDAQ"] == 70
        assert res["KOSPI200"] == 70, "^KS200 1행 → 069500.KS 프록시 폴백"
        tickers = {r[0] for r in db.saved}
        assert tickers == {"^KS11", "^KQ11", "069500.KS"}
        assert len(db.saved) == 210

    def test_fetch_exception_isolated(self):
        def broken(yf_ticker, period):
            if yf_ticker == "^KS11":
                raise RuntimeError("network down")
            return _fake_fetch(70)(yf_ticker, period)

        db = FakeDB()
        res = asyncio.run(backfill_indices(db, fetcher=broken, min_rows=60))
        assert res["KOSPI"] == 0 and res["KOSDAQ"] == 70, "한 지수 실패가 다른 지수를 막지 않음"

    def test_row_shape(self):
        db = FakeDB()
        asyncio.run(backfill_indices(db, fetcher=_fake_fetch(70), min_rows=60))
        row = db.saved[0]
        assert len(row) == 7  # ticker, date, o/h/l/c, volume (ohlcv 스키마 순서)
        assert row[0] == "^KS11" and row[1].startswith("2026-10-")
