# -*- coding: utf-8 -*-
"""tests/unit/test_strategy_router.py - 전략 라우터 실질 검증.

P4-2 정적 검사(ruff F841)에서 기존 테스트가 라우터를 **호출조차 하지 않고**
지역 변수만 검사하는 공허한 테스트임이 드러나,
실제 `StrategyRouter.route()` 계약과 캐싱 동작을 검증하도록 재작성했다.
"""

from typing import Any, Dict

import pytest

from orchestrator.strategy_router import StrategyRouter

TECH: Dict[str, Any] = {"rsi": 45.0, "ema5": 71000.0, "ema20": 69000.0, "volume_ratio": 1.2}


def _data(ticker: str = "005930", price: float = 70000.0, rsi: float = 45.0) -> Dict[str, Any]:
    return {
        "ticker": ticker,
        "price": price,
        "volume": 1000,
        "tech_data": {**TECH, "rsi": rsi},
    }


class TestStrategyRouter:
    def test_is_singleton(self) -> None:
        assert StrategyRouter() is StrategyRouter()

    async def test_route_returns_expected_contract(self) -> None:
        result = await StrategyRouter().route(_data(ticker="000660"))

        assert set(result) >= {
            "final_action", "final_score", "final_confidence",
            "consensus", "action_votes", "strategy_results", "cached",
        }
        assert result["final_action"] in {"BUY", "SELL", "HOLD"}
        assert 0.0 <= float(result["final_confidence"]) <= 1.0

    async def test_route_executes_all_registered_strategies(self) -> None:
        result = await StrategyRouter().route(_data(ticker="035420"))

        assert len(result["strategy_results"]) >= 3
        assert isinstance(result["action_votes"], (dict, list))

    async def test_identical_request_is_cached(self) -> None:
        router = StrategyRouter()
        payload = _data(ticker="051910", price=12345.0)

        first = await router.route(payload)
        second = await router.route(dict(payload))

        assert first["cached"] is False
        assert second["cached"] is True

    async def test_weights_are_normalized(self) -> None:
        router = StrategyRouter()
        total = sum(float(w) for w in router._weights.values())

        assert total == pytest.approx(1.0, abs=0.01)
