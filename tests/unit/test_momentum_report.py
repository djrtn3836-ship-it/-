# -*- coding: utf-8 -*-
"""tests/unit/test_momentum_report.py - 모멘텀 관찰 리포트 검증 (오프라인)."""

from datetime import date, timedelta
from typing import Any, Dict, List

from scheduler.momentum_report import compute_momentum_picks, format_report


def _rows(closes: List[float]) -> List[Dict[str, Any]]:
    base = date(2026, 1, 1)
    return [
        {"date": (base + timedelta(days=i)).isoformat(), "close": c, "open": c, "high": c, "low": c, "volume": 1000}
        for i, c in enumerate(closes)
    ]


class _FakeDB:
    def __init__(self, data: Dict[str, List[Dict[str, Any]]]) -> None:
        self.data = data

    async def get_ohlcv_range(self, ticker: str, start: str, end: str) -> List[Dict[str, Any]]:
        return list(self.data.get(ticker, []))


class TestFormatReport:
    def test_lists_picks(self) -> None:
        picks = [("005930", "삼성전자", 0.25), ("000660", "SK하이닉스", 0.05)]
        text = format_report(picks, lookback=120)
        assert "삼성전자" in text and "005930" in text
        assert "+25.0%" in text
        assert "매매 아님" in text          # 관찰 전용임을 명시
        assert "생존편향" in text

    def test_empty_picks(self) -> None:
        assert "산출 불가" in format_report([])


class TestComputeMomentumPicks:
    async def test_ranks_strongest_first(self) -> None:
        n = 160
        rising = [100.0 * (1.0 + 0.004 * i) for i in range(n)]
        flat = [100.0 for _ in range(n)]
        falling = [100.0 * (1.0 - 0.002 * i) for i in range(n)]
        db = _FakeDB({"A": _rows(rising), "B": _rows(flat), "C": _rows(falling)})

        picks = await compute_momentum_picks(
            db, ["A", "B", "C"], names={"A": "상승", "B": "횡보", "C": "하락"},
            lookback=120, top_k=2, end_date="2026-06-09",
        )

        assert [p[0] for p in picks] == ["A", "B"]
        assert picks[0][1] == "상승"
        assert picks[0][2] > picks[1][2]
        assert picks[1][2] == 0.0   # 횡보 종목은 모멘텀 0

    async def test_insufficient_data(self) -> None:
        db = _FakeDB({"A": _rows([100.0] * 30)})
        picks = await compute_momentum_picks(db, ["A"], lookback=120, top_k=5, end_date="2026-02-01")
        assert picks == []

    async def test_no_data(self) -> None:
        picks = await compute_momentum_picks(_FakeDB({}), ["A"], lookback=120, end_date="2026-06-01")
        assert picks == []
