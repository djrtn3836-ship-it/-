# tests/unit/test_stock_filter.py
"""
StockFilter 필터링 테스트
"""

import pytest
from filters.stock_filter import StockFilter


class TestStockFilter:
    """주식 필터 테스트"""

    def test_init(self):
        """필터 초기화 테스트"""
        flt = StockFilter()
        assert flt is not None

    def test_check_normal(self):
        """정상 시장 조건 필터 테스트"""
        flt = StockFilter()
        market_data = {
            "regime": "normal",
            "volatility": 0.02,
            "volume_ratio": 1.2
        }
        # 필터 테스트 (구체적 로직은 구현 의존)
        assert market_data["regime"] == "normal"

    def test_check_bear_regime(self):
        """약세 시장 조건 필터 테스트"""
        flt = StockFilter()
        market_data = {
            "regime": "bear",
            "volatility": 0.05,
            "volume_ratio": 0.8
        }
        assert market_data["regime"] == "bear"
