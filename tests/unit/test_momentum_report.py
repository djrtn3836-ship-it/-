# -*- coding: utf-8 -*-
"""tests/unit/test_momentum_report.py - 모멘텀 관찰 리포트 검증 (오프라인)."""

from datetime import date, timedelta
from typing import Any, Dict, List

import pytest

from scheduler.momentum_report import (
    compute_momentum_picks,
    evaluate_due_picks,
    format_report,
    record_today_picks,
)


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


class _FakePaperDB(_FakeDB):
    """모의 추적 테이블까지 흉내내는 DB."""

    def __init__(self, data: Dict[str, List[Dict[str, Any]]], pending: List[Dict[str, Any]] | None = None) -> None:
        super().__init__(data)
        self.pending = pending or []
        self.saved: List[Dict[str, Any]] = []
        self.updated: List[tuple] = []

    async def get_momentum_paper_pending(self) -> List[Dict[str, Any]]:
        return list(self.pending)

    async def save_momentum_picks(self, rows: List[Dict[str, Any]]) -> None:
        self.saved.extend(rows)

    async def update_momentum_paper_result(self, pick_id: int, exit_price: float, period_return: float) -> None:
        self.updated.append((pick_id, exit_price, period_return))

    async def get_momentum_paper_stats(self) -> Dict[str, Any]:
        return {"evaluated": 0, "pending": 0, "win_rate": 0.0, "avg_return": 0.0,
                "median_return": 0.0, "best": 0.0, "worst": 0.0}


class TestFormatReport:
    def test_lists_picks(self) -> None:
        picks = [("005930", "삼성전자", 0.25), ("000660", "SK하이닉스", 0.05)]
        text = format_report(picks, lookback=120)
        assert "삼성전자" in text and "005930" in text
        assert "+25.0%" in text
        assert "참고 신호" in text            # 참고 신호(모의)임을 명시
        assert "주문/포지션 아님" in text      # 실거래 아님
        assert "알파 없음" in text             # P5: 베타 분해 결과 명시
        assert "승격 보류" in text

    def test_empty_picks(self) -> None:
        assert "산출 불가" in format_report([])

    def test_includes_paper_stats_when_available(self) -> None:
        stats = {"evaluated": 12, "pending": 8, "win_rate": 0.583,
                 "avg_return": 0.031, "median_return": 0.021, "best": 0.22, "worst": -0.14}
        text = format_report([("005930", "삼성전자", 0.25)], stats=stats)
        assert "모의 추적 성과" in text
        assert "12건" in text and "58.3%" in text

    def test_shows_accumulating_when_no_stats(self) -> None:
        assert "축적 중" in format_report([("005930", "삼성전자", 0.25)], stats=None)


class TestRecordTodayPicks:
    async def test_uses_last_available_bar_as_entry(self) -> None:
        db = _FakePaperDB({"A": _rows([100.0, 110.0, 120.0])})
        n = await record_today_picks(db, [("A", "에이", 0.2)], reference_date="2026-06-09")

        assert n == 1
        assert db.saved[0]["entry_price"] == 120.0
        assert db.saved[0]["pick_date"] == "2026-01-03"
        assert db.saved[0]["rank"] == 1

    async def test_skips_ticker_without_bars(self) -> None:
        db = _FakePaperDB({})
        assert await record_today_picks(db, [("ZZZ", "제트", 0.1)], reference_date="2026-06-09") == 0
        assert db.saved == []

    async def test_empty_picks_is_noop(self) -> None:
        db = _FakePaperDB({"A": _rows([100.0])})
        assert await record_today_picks(db, [], reference_date="2026-06-09") == 0


class TestEvaluateDuePicks:
    async def test_skips_when_hold_period_not_elapsed(self) -> None:
        pending = [{"id": 1, "pick_date": "2026-01-01", "ticker": "A", "entry_price": 100.0}]
        db = _FakePaperDB({"A": _rows([100.0] * 10)}, pending)
        assert await evaluate_due_picks(db, hold_days=20) == 0
        assert db.updated == []

    async def test_computes_return_after_hold_days(self) -> None:
        closes = [100.0] * 20 + [110.0]
        pending = [{"id": 7, "pick_date": "2026-01-01", "ticker": "A", "entry_price": 100.0}]
        db = _FakePaperDB({"A": _rows(closes)}, pending)

        assert await evaluate_due_picks(db, hold_days=20) == 1
        assert db.updated[0][0] == 7
        assert db.updated[0][1] == 110.0
        assert db.updated[0][2] == pytest.approx(0.1)

    async def test_no_pending_is_noop(self) -> None:
        db = _FakePaperDB({"A": _rows([100.0] * 30)})
        assert await evaluate_due_picks(db, hold_days=20) == 0


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
