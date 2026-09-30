# -*- coding: utf-8 -*-
"""tests/unit/test_ohlcv_backfill.py - OHLCV 백필 유틸 검증 (오프라인).

검증 항목:
    TestHelpers    : _safe_float/_safe_int(NaN/None) , _flatten_columns
    TestFetch      : .KS → .KQ 폴백, 실패 시 빈 목록
    TestToRecords  : DB 튜플 변환(스키마 순서), date 누락 스킵
    TestBackfill   : 성공/실패 집계, 저장 행 수, 요약 문자열
"""

from typing import Any, Dict, List

from scheduler.ohlcv_backfill import (
    BackfillResult,
    OHLCVBackfiller,
    _flatten_columns,
    _safe_float,
    _safe_int,
)


class _FakeDB:
    def __init__(self) -> None:
        self.saved: List[tuple] = []

    async def save_ohlcv_batch(self, records: List[tuple]) -> int:
        self.saved.extend(records)
        return len(records)


def _rows(start: str, n: int) -> List[Dict[str, Any]]:
    return [
        {
            "date": f"2026-01-{i + 1:02d}" if i < 28 else start,
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.5 + i,
            "volume": 1000 + i,
        }
        for i in range(n)
    ]


class TestHelpers:
    def test_safe_float(self) -> None:
        assert _safe_float("1.5") == 1.5
        assert _safe_float(float("nan"), 7.0) == 7.0
        assert _safe_float(None, 7.0) == 7.0
        assert _safe_float("abc", 7.0) == 7.0

    def test_safe_int(self) -> None:
        assert _safe_int("10") == 10
        assert _safe_int(float("nan"), 3) == 3
        assert _safe_int(-5, 3) == 3
        assert _safe_int(None, 3) == 3

    def test_flatten_columns(self) -> None:
        import pandas as pd

        df = pd.DataFrame([[1.0, 2.0]], columns=pd.MultiIndex.from_tuples([("Close", "X"), ("Open", "X")]))
        flat = _flatten_columns(df)
        assert list(flat.columns) == ["Close", "Open"]


class TestFetch:
    def test_prefers_kospi_suffix(self) -> None:
        calls: List[str] = []

        def fetcher(yf_ticker: str, period: str) -> List[Dict[str, Any]]:
            calls.append(yf_ticker)
            return _rows("2026-01-01", 3) if yf_ticker.endswith(".KS") else []

        backfiller = OHLCVBackfiller(fetcher=fetcher, delay=0)
        rows, suffix = backfiller.fetch_history("005930", "1y")
        assert suffix == ".KS"
        assert len(rows) == 3
        assert calls == ["005930.KS"]

    def test_falls_back_to_kosdaq(self) -> None:
        def fetcher(yf_ticker: str, period: str) -> List[Dict[str, Any]]:
            return _rows("2026-01-01", 2) if yf_ticker.endswith(".KQ") else []

        backfiller = OHLCVBackfiller(fetcher=fetcher, delay=0)
        rows, suffix = backfiller.fetch_history("900110", "1y")
        assert suffix == ".KQ"
        assert len(rows) == 2

    def test_returns_empty_when_both_fail(self) -> None:
        def fetcher(yf_ticker: str, period: str) -> List[Dict[str, Any]]:
            raise RuntimeError("network down")

        backfiller = OHLCVBackfiller(fetcher=fetcher, delay=0)
        assert backfiller.fetch_history("005930", "1y") == ([], "")


class TestToRecords:
    def test_schema_order(self) -> None:
        records = OHLCVBackfiller.to_records("005930", _rows("2026-01-01", 1))
        assert records[0] == ("005930", "2026-01-01", 100.0, 101.0, 99.0, 100.5, 1000)

    def test_skips_missing_date(self) -> None:
        rows = [{"close": 1.0}, {"date": "2026-01-02", "close": 2.0}]
        records = OHLCVBackfiller.to_records("005930", rows)
        assert len(records) == 1
        assert records[0][1] == "2026-01-02"


class TestBackfill:
    async def test_backfill_counts_and_rows(self) -> None:
        def fetcher(yf_ticker: str, period: str) -> List[Dict[str, Any]]:
            if yf_ticker.startswith("005930"):
                return _rows("2026-01-01", 4)
            if yf_ticker.startswith("000660"):
                return _rows("2026-01-01", 2)
            return []

        db = _FakeDB()
        backfiller = OHLCVBackfiller(fetcher=fetcher, delay=0)
        result = await backfiller.backfill(["005930", "000660", "999999"], db, period="1y")

        assert result.requested == 3
        assert result.succeeded == 2
        assert result.failed == 1
        assert result.failed_tickers == ["999999"]
        assert result.saved_rows == 6
        assert len(db.saved) == 6
        assert "백필 완료" in result.summary()

    async def test_backfill_empty_input(self) -> None:
        db = _FakeDB()
        backfiller = OHLCVBackfiller(fetcher=lambda *_: [], delay=0)
        result = await backfiller.backfill([], db)
        assert result.requested == 0
        assert result.saved_rows == 0

    def test_result_summary_lists_failures(self) -> None:
        result = BackfillResult(requested=3, succeeded=1, failed=2, failed_tickers=["A", "B"])
        assert "실패 2" in result.summary()
