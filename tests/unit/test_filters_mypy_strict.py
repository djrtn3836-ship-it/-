# -*- coding: utf-8 -*-
"""tests/unit/test_filters_mypy_strict.py - Session 24 (6개 파일 검증, 25개)"""

import pytest
from datetime import datetime


class TestMacroFilter:
    def test_check_returns_score_in_range(self):
        from filters.macro_filter import MacroFilter
        result = MacroFilter().check({})
        assert 0.0 <= result["score"] <= 1.0

    def test_check_with_none_values_safe(self):
        from filters.macro_filter import MacroFilter
        result = MacroFilter().check({"kospi_trend": None, "vix": None})
        assert 0.0 <= result["score"] <= 1.0


class TestSectorFilter:
    def test_high_relative_strength_full_score(self):
        from filters.sector_filter import SectorFilter
        result = SectorFilter().check({"sector_relative": 1.10, "sector_money_flow": 100, "sector_rank": 5})
        assert result["score"] == 1.0

    def test_low_conditions_zero_score(self):
        from filters.sector_filter import SectorFilter
        result = SectorFilter().check({"sector_relative": 0.90, "sector_money_flow": -100, "sector_rank": 80})
        assert result["score"] == 0.0


class TestDynamicWeighter:
    @pytest.mark.parametrize("regime", ["Bull", "Sideways", "Bear", "Panic", "Recovery"])
    def test_weights_sum_to_one(self, regime):
        from filters.dynamic_weighter import DynamicWeighter
        result = DynamicWeighter().calculate({"regime": regime})
        total = result["trend_weight"] + result["risk_weight"] + result["flow_weight"]
        assert abs(total - 1.0) < 0.02

    def test_unknown_regime_fallback_to_sideways(self):
        from filters.dynamic_weighter import DynamicWeighter
        result = DynamicWeighter().calculate({"regime": "Unknown"})
        assert "trend_weight" in result


class TestKoreanSpecialFilter:
    def test_normal_time_passes(self):
        from filters.korean_special_filter import KoreanSpecialFilter
        t = datetime.now().replace(hour=10, minute=30, second=0, microsecond=0)
        result = KoreanSpecialFilter().check({"ticker": "005930", "price": 70000, "current_time": t})
        assert result["passed"] is True

    def test_pre_open_blocked(self):
        from filters.korean_special_filter import KoreanSpecialFilter
        t = datetime.now().replace(hour=8, minute=45, second=0, microsecond=0)
        result = KoreanSpecialFilter().check({"ticker": "005930", "price": 70000, "current_time": t})
        assert result["passed"] is False

    def test_string_price_does_not_crash(self):
        from filters.korean_special_filter import KoreanSpecialFilter
        t = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
        result = KoreanSpecialFilter().check({"ticker": "005930", "price": "70000", "current_time": t})
        assert isinstance(result["score"], float)


class TestStockFilter:
    def test_regime_alias_correction_maps_to_bear(self):
        from filters.stock_filter import StockFilter
        result = StockFilter().check({"price": 50000}, regime="Correction")
        assert result["regime_used"] == "Bear"

    def test_regime_alias_recovery_maps_to_bull(self):
        from filters.stock_filter import StockFilter
        result = StockFilter().check({"price": 50000}, regime="Recovery")
        assert result["regime_used"] == "Bull"

    def test_to_float_safe_defaults(self):
        from filters.stock_filter import StockFilter
        f = StockFilter()
        assert f._to_float(None) == 0.0
        assert f._to_float("abc") == 0.0
        assert f._to_float("3.14") == pytest.approx(3.14)


class TestAtrService:
    @pytest.mark.asyncio
    async def test_price_fallback_used_when_db_empty(self):
        from application.analysis.atr_service import AtrService

        class DummyDB:
            async def get_ohlcv(self, ticker, period):
                return []

        service = AtrService(db_manager=DummyDB(), realtime_price_provider=lambda t: 70000.0)
        atr = await service.calculate("005930")
        assert atr == pytest.approx(70000.0 * service._fallback_ratio)

    @pytest.mark.asyncio
    async def test_complete_failure_returns_zero(self):
        from application.analysis.atr_service import AtrService

        class DummyDB:
            async def get_ohlcv(self, ticker, period):
                return []

        service = AtrService(db_manager=DummyDB(), realtime_price_provider=None)
        assert await service.calculate("999999") == 0.0

    def test_clear_cache_by_ticker_prefix(self):
        from application.analysis.atr_service import AtrService
        service = AtrService()
        service._cache["005930:14"] = 100.0
        service._cache["000660:14"] = 200.0
        service.clear_cache("005930")
        assert "005930:14" not in service._cache
        assert "000660:14" in service._cache
