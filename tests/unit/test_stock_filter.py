# -*- coding: utf-8 -*-
"""tests/unit/test_stock_filter.py - 종목 필터 실질 검증.

P4-2 정적 검사(ruff F841)에서 기존 테스트가 필터를 **호출조차 하지 않는**
공허한 테스트임이 드러나, 실제 `StockFilter.check()` 계약을 검증하도록 재작성했다.
"""

from typing import Any, Dict

import pytest

from filters.stock_filter import StockFilter

STRONG = {
    "price": 10000.0,
    "ma_20": 9000.0,
    "rsi": 25.0,            # 과매도 → 매수 우호
    "volume_ratio": 1.8,
    "institution_net": 500.0,
    "adx": 35.0,
    "eps_growth": 0.2,
    "roe": 0.15,
    "fcf": 100.0,
}

WEAK: Dict[str, Any] = {
    "price": 8000.0,
    "ma_20": 9000.0,        # 이평선 하회
    "rsi": 75.0,            # 과매수
    "volume_ratio": 0.6,
    "institution_net": -500.0,
}


class TestStockFilter:
    def test_returns_expected_contract(self) -> None:
        result = StockFilter().check(STRONG, regime="Bull")

        assert set(result) >= {"score", "details", "passed", "feature_count", "regime_used"}
        assert result["feature_count"] == 13
        assert isinstance(result["passed"], bool)
        assert isinstance(result["score"], float)

    def test_grades_strong_setup_above_weak(self) -> None:
        flt = StockFilter()
        strong = flt.check(STRONG, regime="Bull")
        weak = flt.check(WEAK, regime="Bull")

        assert strong["score"] > weak["score"]

    def test_regime_alias_is_resolved(self) -> None:
        flt = StockFilter()
        result = flt.check(STRONG, regime="Panic")

        assert result["original_regime"] == "Panic"
        assert result["regime_used"] == "Bear"  # Panic → Bear 별칭

    def test_handles_missing_fields_without_raising(self) -> None:
        result = StockFilter().check({}, regime="Sideways")

        assert result["feature_count"] == 13
        assert isinstance(result["score"], float)

    @pytest.mark.parametrize("regime", ["Bull", "Bear", "Sideways", "Correction", "Recovery"])
    def test_all_regimes_produce_score(self, regime: str) -> None:
        assert isinstance(StockFilter().check(STRONG, regime=regime)["score"], float)
