"""
tests/unit/test_strategy_router.py - StrategyRouter 단위 테스트
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import pytest

from orchestrator.strategy_router import StrategyRouter


class TestStrategyRouter:
    """StrategyRouter 단위 테스트"""

    def setup_method(self):
        self.router = StrategyRouter()

    @pytest.mark.asyncio
    async def test_route_normal(self, sample_stock_data):
        """정상적인 전략 라우팅 테스트"""
        result = await self.router.route(sample_stock_data)

        assert "final_score" in result
        assert "final_action" in result
        assert "final_confidence" in result
        assert "strategy_results" in result
        assert "consensus" in result
        assert "action_votes" in result
        assert 0.0 <= result["final_score"] <= 1.0
        assert result["final_action"] in ["BUY", "SELL", "HOLD"]
        assert len(result["strategy_results"]) > 0
        print(f"✅ 정상 라우팅 테스트 통과: {result['final_action']} (score {result['final_score']:.3f})")

    @pytest.mark.asyncio
    async def test_route_caching(self, sample_stock_data):
        """캐싱 테스트 (동일 데이터 중복 호출 방지)"""
        # 첫 번째 호출
        result1 = await self.router.route(sample_stock_data)
        # 두 번째 호출 (동일 데이터)
        result2 = await self.router.route(sample_stock_data)

        assert result1["final_score"] == result2["final_score"]
        assert result1["final_action"] == result2["final_action"]
        assert result2.get("cached") is True
        print("✅ 캐싱 테스트 통과: 두 번째 호출에서 캐시 사용됨")

    @pytest.mark.asyncio
    async def test_route_exception_isolation(self):
        """예외 격리 테스트 (한 전략 실패 시 나머지는 정상)"""
        # 비정상 데이터 (price=0)로 테스트
        bad_data = {"ticker": "ERROR", "price": 0.0, "tech_data": {}, "regime": "Sideways"}
        result = await self.router.route(bad_data)

        # 일부 전략은 실패했지만, 최종 결과는 반환되어야 함
        assert "final_score" in result
        assert "final_action" in result
        print(f"✅ 예외 격리 테스트 통과: {result['final_action']} (score {result['final_score']:.3f})")

    @pytest.mark.asyncio
    async def test_weight_normalization(self, sample_stock_data):
        """가중치 정규화 테스트 (합계 1.0)"""
        result = await self.router.route(sample_stock_data)

        total_weight = sum(r.weight for r in result.get("strategy_results", []))
        # 가중치 합계가 1.0에 매우 가까워야 함 (부동소수점 오차 허용)
        assert abs(total_weight - 1.0) < 0.01
        print(f"✅ 가중치 정규화 테스트 통과: 합계 {total_weight:.4f}")

    @pytest.mark.asyncio
    async def test_action_vote_consensus(self, sample_stock_data):
        """가중 투표 합의 테스트"""
        result = await self.router.route(sample_stock_data)

        votes = result["action_votes"]
        # BUY, SELL, HOLD 중 하나는 0보다 커야 함
        assert sum(votes.values()) > 0
        # 합의 문자열에 '개 전략 일치'가 포함되어야 함
        assert "개 전략 일치" in result["consensus"] or "전략" in result["consensus"]
        print(f"✅ 투표 합의 테스트 통과: BUY {votes['BUY']:.2f}, SELL {votes['SELL']:.2f}, HOLD {votes['HOLD']:.2f}")
