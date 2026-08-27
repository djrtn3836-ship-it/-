"""
tests/unit/test_feature_store_v2.py - v1.1 (Session 12, 자체 검증 수정본)
FeatureStore v2.0 단위 테스트 (42개)
"""

import asyncio
import pytest
from dataclasses import FrozenInstanceError

from orchestrator.feature_store import (
    BatchFeatureComputer,
    FeatureLineage,
    FeatureStore,
    FeatureValidationResult,
    FeatureValidator,
    compute_atr,
    compute_bollinger,
    compute_ema,
    compute_macd,
    compute_rsi,
    compute_stochastic,
)


def make_ohlcv(n: int = 30, base: float = 100.0) -> list:
    data = []
    price = base
    for i in range(n):
        price += (i % 5) - 2
        price = max(1.0, price)
        data.append({
            "open": price - 0.5, "high": price + 1.0,
            "low": price - 1.0, "close": price,
            "volume": 1000.0 + i * 10,
        })
    return data


class TestComputeEma:
    def test_empty_returns_zero(self):
        assert compute_ema([], 5) == 0.0

    def test_insufficient_data_returns_last(self):
        assert compute_ema([10.0, 20.0], 5) == 20.0

    def test_single_value(self):
        assert compute_ema([42.0], 1) == pytest.approx(42.0)

    def test_ema_converges(self):
        assert compute_ema([100.0] * 20, 5) == pytest.approx(100.0, abs=1e-6)


class TestComputeRsi:
    def test_insufficient_data_returns_50(self):
        assert compute_rsi([100.0] * 5, 14) == 50.0

    def test_all_gains_returns_100(self):
        prices = [float(i) for i in range(1, 20)]
        assert compute_rsi(prices, 14) == pytest.approx(100.0)

    def test_all_losses_returns_0(self):
        prices = [float(20 - i) for i in range(20)]
        assert compute_rsi(prices, 14) == pytest.approx(0.0, abs=1e-6)

    def test_range_0_to_100(self):
        closes = [d["close"] for d in make_ohlcv(30)]
        assert 0.0 <= compute_rsi(closes, 14) <= 100.0


class TestComputeBollinger:
    def test_insufficient_data_fallback(self):
        upper, mid, lower, pct_b = compute_bollinger([100.0], 20)
        assert upper == pytest.approx(105.0)
        assert pct_b == pytest.approx(0.5)

    def test_flat_prices_zero_std(self):
        upper, mid, lower, pct_b = compute_bollinger([100.0] * 25, 20)
        assert upper == pytest.approx(100.0)
        assert lower == pytest.approx(100.0)
        assert pct_b == pytest.approx(0.5)

    def test_pct_b_in_range(self):
        closes = [d["close"] for d in make_ohlcv(30)]
        _, _, _, pct_b = compute_bollinger(closes, 20)
        assert 0.0 <= pct_b <= 1.0

    def test_empty_returns_zeros(self):
        upper, mid, lower, pct_b = compute_bollinger([])
        assert upper == 0.0 and mid == 0.0 and lower == 0.0


class TestComputeMacd:
    def test_insufficient_data_returns_zeros(self):
        assert compute_macd([100.0] * 10) == (0.0, 0.0, 0.0)

    def test_returns_tuple_of_three(self):
        closes = [d["close"] for d in make_ohlcv(50)]
        assert len(compute_macd(closes)) == 3

    def test_histogram_equals_macd_minus_signal(self):
        closes = [d["close"] for d in make_ohlcv(50)]
        macd, signal, hist = compute_macd(closes)
        assert hist == pytest.approx(macd - signal, abs=1e-9)


class TestComputeAtr:
    def test_insufficient_data_returns_zero(self):
        assert compute_atr([100.0], [99.0], [99.5]) == 0.0

    def test_flat_market_near_zero(self):
        n = 20
        assert compute_atr([100.0] * n, [100.0] * n, [100.0] * n, 14) == pytest.approx(0.0)

    def test_volatile_market_positive(self):
        n = 20
        atr = compute_atr([110.0] * n, [90.0] * n, [100.0] * n, 14)
        assert atr > 0


class TestComputeStochastic:
    def test_insufficient_data_returns_50_50(self):
        assert compute_stochastic([100.0] * 5, [99.0] * 5, [99.5] * 5, 14) == (50.0, 50.0)

    def test_range_0_to_100(self):
        ohlcv = make_ohlcv(30)
        highs  = [d["high"]  for d in ohlcv]
        lows   = [d["low"]   for d in ohlcv]
        closes = [d["close"] for d in ohlcv]
        k, d = compute_stochastic(highs, lows, closes, 14, 3)
        assert 0.0 <= k <= 100.0
        assert 0.0 <= d <= 100.0

    def test_flat_market_returns_50(self):
        n = 20
        k, d = compute_stochastic([100.0] * n, [100.0] * n, [100.0] * n, 14, 3)
        assert k == pytest.approx(50.0)


class TestFeatureValidator:
    def test_valid_features(self):
        result = FeatureValidator.validate({
            "rsi": 55.0, "pct_b": 0.6, "volume_ratio": 1.2, "atr": 500.0
        })
        assert result.is_valid is True
        assert result.invalid_features == []

    def test_nan_detected(self):
        result = FeatureValidator.validate({"rsi": float("nan")})
        assert result.is_valid is False
        assert "rsi" in result.invalid_features

    def test_inf_detected(self):
        assert FeatureValidator.validate({"rsi": float("inf")}).is_valid is False

    def test_out_of_range_rsi(self):
        result = FeatureValidator.validate({"rsi": 150.0})
        assert result.is_valid is False
        assert "rsi" in result.invalid_features

    def test_unknown_feature_ignored(self):
        assert FeatureValidator.validate({"custom_score": 999.0}).is_valid is True

    def test_frozen_result(self):
        result = FeatureValidator.validate({"rsi": 50.0})
        with pytest.raises(FrozenInstanceError):
            result.is_valid = False

    def test_validation_result_is_correct_type(self):
        # 타입 검증: FeatureValidationResult 클래스 자체를 실제로 사용
        result = FeatureValidator.validate({"rsi": 50.0})
        assert isinstance(result, FeatureValidationResult)


class TestFeatureStore:
    def test_empty_ohlcv_returns_empty(self):
        store = FeatureStore()
        result = asyncio.run(store.compute_all_features("005930", []))
        assert result == {}

    def test_single_row_returns_empty(self):
        store = FeatureStore()
        result = asyncio.run(store.compute_all_features("005930", [make_ohlcv(1)[0]]))
        assert result == {}

    def test_returns_expected_keys(self):
        store = FeatureStore()
        result = asyncio.run(store.compute_all_features("005930", make_ohlcv(30)))
        for key in ["rsi", "ema5", "ema20", "pct_b", "macd_hist", "atr", "pct_k", "volume_ratio"]:
            assert key in result

    def test_cache_hit(self):
        store = FeatureStore(ttl=60)
        ohlcv = make_ohlcv(30)
        r1 = asyncio.run(store.compute_all_features("005930", ohlcv))
        r2 = asyncio.run(store.compute_all_features("005930", ohlcv))
        assert r1 == r2

    def test_lineage_recorded(self):
        store = FeatureStore()
        asyncio.run(store.compute_all_features("005930", make_ohlcv(30)))
        lineage = store.get_lineage("005930")
        assert lineage is not None
        assert lineage.ticker == "005930"
        assert lineage.ohlcv_rows == 30

    def test_lineage_is_correct_type(self):
        # 타입 검증: FeatureLineage 클래스 자체를 실제로 사용
        store = FeatureStore()
        asyncio.run(store.compute_all_features("005930", make_ohlcv(30)))
        assert isinstance(store.get_lineage("005930"), FeatureLineage)

    def test_invalidate_removes_cache(self):
        store = FeatureStore(ttl=60)
        asyncio.run(store.compute_all_features("005930", make_ohlcv(30)))
        assert store.invalidate("005930") is True

    def test_invalidate_nonexistent_returns_false(self):
        assert FeatureStore().invalidate("nonexistent") is False

    def test_validate_features_valid(self):
        store = FeatureStore()
        features = asyncio.run(store.compute_all_features("005930", make_ohlcv(30)))
        result = asyncio.run(store.validate_features(features))
        assert result.is_valid is True

    def test_get_stats(self):
        store = FeatureStore()
        asyncio.run(store.compute_all_features("005930", make_ohlcv(30)))
        stats = asyncio.run(store.get_stats())
        assert stats["total_tickers"] == 1

    def test_get_stats_fresh_rate_zero_when_no_cache_hit(self):
        # daily_monitor.py의 "PASS"/"WARN" 판정과 호환되는 실계산 확인
        store = FeatureStore()
        asyncio.run(store.compute_all_features("005930", make_ohlcv(30)))
        stats = asyncio.run(store.get_stats())
        assert stats["fresh_rate"] == pytest.approx(0.0)

    def test_clear(self):
        store = FeatureStore()
        asyncio.run(store.compute_all_features("005930", make_ohlcv(30)))
        store.clear()
        assert store.get_lineage("005930") is None

    def test_get_lineage_report_not_found(self):
        assert "error" in FeatureStore().get_lineage_report("nonexistent")


class TestBatchFeatureComputer:
    def test_empty_input_returns_empty(self):
        computer = BatchFeatureComputer(FeatureStore())
        assert asyncio.run(computer.compute_batch({})) == {}

    def test_multiple_tickers(self):
        computer = BatchFeatureComputer(FeatureStore())
        result = asyncio.run(computer.compute_batch({
            "005930": make_ohlcv(30),
            "000660": make_ohlcv(30, base=50.0),
        }))
        assert "005930" in result
        assert "000660" in result

    def test_validate_batch(self):
        store = FeatureStore()
        computer = BatchFeatureComputer(store)
        batch = asyncio.run(computer.compute_batch({"005930": make_ohlcv(30)}))
        validation = asyncio.run(computer.validate_batch(batch))
        assert validation["005930"].is_valid is True
