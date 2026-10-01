# -*- coding: utf-8 -*-
"""tests/unit/test_market_risk_monitor.py - 서킷브레이커 배선 검증 (P3-1 후속).

검증 항목:
    1. 시장 입력(변동성/거래량 비율) 계산
    2. 데이터 부족 시 무해한 기본값
    3. 매니저 싱글톤
    4. cb_input_provider → RCA용 CircuitBreakerInput 변환
    5. 변동성 임계 초과 시 차단기 OPEN + 알림 1회
"""

from datetime import date, timedelta
from typing import Any, Dict, List

import pytest

from risk import market_risk_monitor as mrm
from risk.market_risk_monitor import (
    cb_input_provider,
    compute_market_inputs,
    evaluate_market_risk,
    get_circuit_breaker_manager,
)


def _rows(closes: List[float], volumes: List[float] | None = None) -> List[Dict[str, Any]]:
    base = date(2026, 1, 1)
    vols = volumes or [1000.0] * len(closes)
    return [
        {
            "date": (base + timedelta(days=i)).isoformat(),
            "close": c, "open": c, "high": c, "low": c, "volume": vols[i],
        }
        for i, c in enumerate(closes)
    ]


class _FakeDB:
    def __init__(self, data: Dict[str, List[Dict[str, Any]]], daily_volume: List[Dict[str, Any]] | None = None) -> None:
        self.data = data
        self.daily_volume = daily_volume or []
        self.closed = False

    async def get_ohlcv_range(self, ticker: str, start: str, end: str) -> List[Dict[str, Any]]:
        return list(self.data.get(ticker, []))

    async def get_daily_total_volume(self, days: int = 21) -> List[Dict[str, Any]]:
        return list(self.daily_volume)

    async def get_latest_momentum_paper_return(self) -> float | None:
        return None

    async def close(self) -> None:
        self.closed = True


class TestComputeMarketInputs:
    async def test_computes_volatility_and_volume_ratio(self) -> None:
        n = 30
        # 완만한 상승(변동성 낮음) + 마지막 날 큰 하락(변동성 발생)
        closes = [100.0 + 0.5 * i for i in range(n - 1)] + [80.0]
        db = _FakeDB(
            {f"T{i}": _rows(closes) for i in range(8)},
            daily_volume=[{"total_volume": v} for v in [2000.0, 1000.0, 1000.0, 1000.0]],
        )

        vol, vr = await compute_market_inputs(db, list(db.data.keys()))

        assert vol > 0.0
        assert vr == pytest.approx(2.0)  # 2000 / avg(1000,1000,1000)

    async def test_insufficient_data_returns_neutral(self) -> None:
        db = _FakeDB({"A": _rows([100.0, 101.0])})
        assert await compute_market_inputs(db, ["A"]) == (0.0, 1.0)

    async def test_no_tickers_returns_neutral(self) -> None:
        assert await compute_market_inputs(_FakeDB({}), []) == (0.0, 1.0)


class TestManagerAndProvider:
    def test_manager_is_singleton(self) -> None:
        assert get_circuit_breaker_manager() is get_circuit_breaker_manager()

    def test_provider_shape(self) -> None:
        get_circuit_breaker_manager().reset_all()
        info = cb_input_provider()

        assert info.is_open is False
        assert info.open_count == 0
        assert info.breaker_names == []
        assert "Volatility" in info.status_summary


class TestEvaluateMarketRisk:
    async def test_opens_volatility_breaker_and_alerts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_circuit_breaker_manager().reset_all()
        alerts: List[Dict[str, Any]] = []

        async def _fake_inputs(db: Any, tickers: Any, window: int = 20):
            return 0.10, 1.0  # 변동성 10% > 임계 4%

        async def _fake_alert(summary: Dict[str, Any]) -> None:
            alerts.append(summary)

        monkeypatch.setattr(mrm, "compute_market_inputs", _fake_inputs)
        monkeypatch.setattr(mrm, "_send_alert", _fake_alert)
        monkeypatch.setattr("data.db_manager.DatabaseManager", lambda: _FakeDB({}))

        summary = await evaluate_market_risk(send_alert=True)

        assert summary["is_open"] is True
        assert "Volatility" in summary["open_breakers"]
        assert len(alerts) == 1

        get_circuit_breaker_manager().reset_all()

    async def test_stays_closed_under_thresholds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_circuit_breaker_manager().reset_all()
        alerts: List[Dict[str, Any]] = []

        async def _fake_inputs(db: Any, tickers: Any, window: int = 20):
            return 0.01, 1.0

        async def _fake_alert(summary: Dict[str, Any]) -> None:
            alerts.append(summary)

        monkeypatch.setattr(mrm, "compute_market_inputs", _fake_inputs)
        monkeypatch.setattr(mrm, "_send_alert", _fake_alert)
        monkeypatch.setattr("data.db_manager.DatabaseManager", lambda: _FakeDB({}))

        summary = await evaluate_market_risk(send_alert=True)

        assert summary["is_open"] is False
        assert alerts == []
