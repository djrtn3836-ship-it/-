# -*- coding: utf-8 -*-
"""tests/unit/test_correlation_monitor.py - 집중도 리스크 모니터 검증 (P8-1)."""

from typing import Any, Dict, List

import pytest

from risk import correlation_monitor as cm


class _FakeDB:
    """종목별 봉 데이터를 돌려주는 가짜 DB(결측일 시뮬레이션 지원)."""

    def __init__(self, data: Dict[str, Dict[str, float]]) -> None:
        self.data = data

    async def _execute_read(self, query: str, params: Any = ()) -> List[Dict[str, Any]]:
        if "SUM(volume)" in query:
            limit = int(params[0]) if params else len(self.data)
            return [{"ticker": t} for t in list(self.data)[:limit]]
        return []

    async def get_ohlcv_range(self, ticker: str, start: str, end: str) -> List[Dict[str, Any]]:
        return [
            {"date": d, "close": c}
            for d, c in sorted(self.data.get(ticker, {}).items())
            if start <= d <= end
        ]


def _dates(n: int) -> List[str]:
    """오늘 기준 최근 n일(모니터가 기간 필터를 걸므로 최근이어야 한다)."""
    from datetime import datetime, timedelta

    today = datetime.now()
    return [(today - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d") for i in range(n)]


def _series(n: int, base: float = 100.0, step: float = 0.01) -> Dict[str, float]:
    """단조 증가 시계열 생성(결정적)."""
    out = {}
    price = base
    for d in _dates(n):
        out[d] = round(price, 4)
        price *= 1 + step
    return out


class TestReturnAlignment:
    async def test_missing_day_does_not_create_fake_correlation(self) -> None:
        """결측일이 있는 종목과 완전한 종목이 뒤섞여도 위치 어긋남이 없어야 한다.

        결측 종목은 공통 거래일 교집합 기준으로만 평가되므로,
        무관한 랜덤 데이터에서 ρ가 1.0에 가까워지면 정렬 버그다.
        """
        import random

        rng = random.Random(42)
        dates = _dates(30)

        def _walk() -> Dict[str, float]:
            p, out = 100.0, {}
            for d in dates:
                p *= 1 + rng.uniform(-0.03, 0.03)
                out[d] = round(p, 4)
            return out

        a, b = _walk(), _walk()
        # b에서 5일 결측
        for d in dates[10:15]:
            b.pop(d)

        db = _FakeDB({"A": a, "B": b})
        report = await cm.compute_correlation_report(db, window=20, max_tickers=2)

        if report["status"] == "ok":
            pairs = report["top_pairs"]
            if pairs:
                # 정렬 버그라면 |ρ|가 0.99 수준으로 나온다
                assert abs(float(pairs[0]["correlation"])) < 0.95, "결측일로 인한 가짜 상관 의심"

    async def test_common_dates_reported(self) -> None:
        db = _FakeDB({"A": _series(30), "B": _series(30)})
        report = await cm.compute_correlation_report(db, window=20, max_tickers=2)

        assert report["status"] == "ok"
        assert report["common_dates"] == 30

    async def test_insufficient_common_dates(self) -> None:
        a = _series(30)
        b = {d: v for d, v in list(_series(30).items())[:10]}
        db = _FakeDB({"A": a, "B": b})

        report = await cm.compute_correlation_report(db, window=20, max_tickers=2)
        assert report["status"] == "insufficient_data"


class TestReportShape:
    async def test_identical_series_flagged_high(self) -> None:
        same = _series(40)
        db = _FakeDB({"A": dict(same), "B": dict(same)})

        report = await cm.compute_correlation_report(db, window=30, max_tickers=2)

        assert report["status"] == "ok"
        assert report["high_corr_pair_count"] >= 1
        assert report["score"] < 0.5
        assert report["top_pairs"][0]["correlation"] == pytest.approx(1.0, abs=0.01)

    async def test_report_fields(self) -> None:
        db = _FakeDB({"A": _series(40), "B": _series(40, step=-0.01)})
        report = await cm.compute_correlation_report(db, window=30, max_tickers=2)

        for key in ("status", "as_of", "window", "evaluated_tickers", "score",
                    "avg_abs_correlation", "high_corr_pair_count", "top_pairs", "recommendation"):
            assert key in report, f"{key} 누락"

    async def test_single_ticker_is_not_ok(self) -> None:
        db = _FakeDB({"A": _series(40)})
        report = await cm.compute_correlation_report(db, window=30, max_tickers=1)
        assert report["status"] in ("insufficient_data", "insufficient_tickers")

    async def test_short_series_excluded(self) -> None:
        db = _FakeDB({"A": _series(40), "B": _series(5)})
        report = await cm.compute_correlation_report(db, window=30, max_tickers=2)
        assert report["status"] in ("insufficient_data", "insufficient_tickers")


class TestScheduledCheck:
    async def test_error_is_contained(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DB 전면 장애 시에도 예외가 새지 않고 상태로 보고되어야 한다."""
        class _Broken:
            async def _execute_read(self, *a: Any, **k: Any) -> Any:
                raise RuntimeError("DB 오류")

            async def get_ohlcv_range(self, *a: Any, **k: Any) -> Any:
                raise RuntimeError("DB 오류")

            async def close(self) -> None:
                return None

        monkeypatch.setattr("data.db_manager.DatabaseManager", _Broken)
        result = await cm.scheduled_correlation_check()

        assert result["status"] in ("error", "insufficient_data")
        assert "status" in result

    async def test_alert_triggered_on_many_pairs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent: List[str] = []

        class _Sender:
            async def send_raw(self, text: str) -> None:
                sent.append(text)

        monkeypatch.setattr("report.telegram_sender.TelegramSender", _Sender)
        monkeypatch.setattr(cm, "_last_alert_at", 0.0)

        await cm._notify({
            "score": 0.3, "avg_abs_correlation": 0.7, "high_corr_pair_count": 9,
            "evaluated_tickers": 30,
            "top_pairs": [{"ticker_a": "005930", "ticker_b": "000660", "correlation": 0.95}],
        })

        assert sent and "집중도 리스크" in sent[0]

    async def test_alert_cooldown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent: List[str] = []

        class _Sender:
            async def send_raw(self, text: str) -> None:
                sent.append(text)

        monkeypatch.setattr("report.telegram_sender.TelegramSender", _Sender)
        monkeypatch.setattr(cm, "_last_alert_at", 0.0)
        payload = {"score": 0.3, "avg_abs_correlation": 0.7, "high_corr_pair_count": 9,
                   "evaluated_tickers": 30, "top_pairs": []}

        await cm._notify(payload)
        await cm._notify(payload)     # 쿨다운 내 → 생략

        assert len(sent) == 1


class TestLastReport:
    def test_get_last_report_empty_initially(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cm, "_last_report", None)
        assert cm.get_last_report() == {}

    def test_get_last_report_returns_copy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cm, "_last_report", {"score": 0.5})
        got = cm.get_last_report()
        got["score"] = 0.9

        assert cm.get_last_report()["score"] == 0.5
