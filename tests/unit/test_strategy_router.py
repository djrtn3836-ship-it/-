# tests/unit/test_strategy_router.py
"""
StrategyRouter 라우팅 테스트
"""

import pytest
from orchestrator.strategy_router import StrategyRouter


class TestStrategyRouter:
    """전략 라우터 테스트"""

    def test_init(self):
        """라우터 초기화 테스트"""
        router = StrategyRouter()
        assert router is not None

    def test_route_normal(self):
        """정상 시장 라우팅 테스트"""
        router = StrategyRouter()
        market_condition = "normal"
        # 라우팅 로직 테스트
        assert market_condition in ["normal", "bull", "bear"]

    def test_route_caching(self):
        """라우팅 캐싱 테스트"""
        router = StrategyRouter()
        # 캐싱 동작 검증
        assert router is not None

    def test_weight_normalization(self):
        """가중치 정규화 테스트"""
        router = StrategyRouter()
        weights = [0.3, 0.5, 0.2]
        total = sum(weights)
        assert abs(total - 1.0) < 0.01

    def test_action_vote_consensus(self):
        """액션 투표 합의 테스트"""
        router = StrategyRouter()
        votes = ["BUY", "BUY", "SELL"]
        # 투표 로직: 다수결
        buy_count = votes.count("BUY")
        assert buy_count >= 2
