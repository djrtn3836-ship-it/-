"""
orchestrator/feature_store.py - v2.0 (Session 12)

Feature Store: OHLCV → 기술지표 자동 계산 파이프라인
- 순수 Python 기술지표 계산 (EMA/RSI/BB/MACD/ATR/Stochastic)
- FeatureValidator: 범위 검증, NaN/Inf 탐지
- BatchFeatureComputer: 여러 종목 동시 계산 (asyncio.gather)
- TTL 기반 캐시 (cachetools.TTLCache)
"""

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from cachetools import TTLCache

from core.logger import setup_logger

logger = setup_logger("feature_store")

_DEFAULT_TTL = 300
_DEFAULT_MAXSIZE = 1000
_MIN_OHLCV_ROWS = 2


# ═══════════════════════════════════════════════════════════════════
#  순수 Python 기술지표 계산 (모듈 수준 함수)
# ═══════════════════════════════════════════════════════════════════

def compute_ema(prices: List[float], n: int) -> float:
    """지수이동평균(EMA). 데이터 부족 시 마지막 값 반환."""
    if not prices:
        return 0.0
    if len(prices) < n:
        return prices[-1]
    k = 2.0 / (n + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema


def compute_rsi(prices: List[float], n: int = 14) -> float:
    """상대강도지수(RSI). 데이터 부족 시 50 반환."""
    if len(prices) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[-n:]) / n
    avg_loss = sum(losses[-n:]) / n
    if avg_loss < 1e-9:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def compute_bollinger(
    prices: List[float], n: int = 20, k: float = 2.0
) -> Tuple[float, float, float, float]:
    """볼린저 밴드 + %B. 데이터 부족 시 last±5%, pct_b=0.5 반환."""
    if not prices:
        return 0.0, 0.0, 0.0, 0.5
    last = prices[-1]
    if len(prices) < n:
        return last * 1.05, last, last * 0.95, 0.5
    window = prices[-n:]
    mean = sum(window) / n
    variance = sum((p - mean) ** 2 for p in window) / n
    std = math.sqrt(variance) if variance > 0 else 0.0
    upper = mean + k * std
    lower = mean - k * std
    band_width = upper - lower
    pct_b = (last - lower) / band_width if band_width > 1e-9 else 0.5
    pct_b = max(0.0, min(1.0, pct_b))
    return upper, mean, lower, pct_b


def compute_macd(
    prices: List[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Tuple[float, float, float]:
    """MACD. 데이터 부족 시 (0.0, 0.0, 0.0) 반환."""
    if len(prices) < slow + signal_period:
        return 0.0, 0.0, 0.0
    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)
    ema_fast = prices[0]
    ema_slow = prices[0]
    macd_history: List[float] = []
    for p in prices[1:]:
        ema_fast = p * k_fast + ema_fast * (1 - k_fast)
        ema_slow = p * k_slow + ema_slow * (1 - k_slow)
        macd_history.append(ema_fast - ema_slow)
    if len(macd_history) < signal_period:
        return 0.0, 0.0, 0.0
    macd_line = macd_history[-1]
    signal_line = compute_ema(macd_history, signal_period)
    return macd_line, signal_line, macd_line - signal_line


def compute_atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    n: int = 14,
) -> float:
    """Average True Range. 데이터 부족 시 0.0 반환."""
    length = min(len(highs), len(lows), len(closes))
    if length < 2:
        return 0.0
    tr_values: List[float] = []
    for i in range(1, length):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_values.append(tr)
    if not tr_values:
        return 0.0
    recent = tr_values[-n:]
    return sum(recent) / len(recent)


def compute_stochastic(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    k_period: int = 14,
    d_period: int = 3,
) -> Tuple[float, float]:
    """Stochastic Oscillator (%K, %D). 데이터 부족 시 (50.0, 50.0) 반환."""
    length = min(len(highs), len(lows), len(closes))
    if length < k_period:
        return 50.0, 50.0
    k_values: List[float] = []
    for i in range(k_period - 1, length):
        window_high = max(highs[i - k_period + 1: i + 1])
        window_low = min(lows[i - k_period + 1: i + 1])
        denom = window_high - window_low
        if denom < 1e-9:
            k_values.append(50.0)
        else:
            k_values.append((closes[i] - window_low) / denom * 100)
    if not k_values:
        return 50.0, 50.0
    pct_k = k_values[-1]
    recent_k = k_values[-d_period:] if len(k_values) >= d_period else k_values
    pct_d = sum(recent_k) / len(recent_k)
    return pct_k, pct_d


# ═══════════════════════════════════════════════════════════════════
#  DTO
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FeatureValidationResult:
    """피처 검증 결과 DTO"""
    is_valid: bool
    invalid_features: List[str]
    warnings: List[str]

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "invalid_features": self.invalid_features,
            "warnings": self.warnings,
        }


@dataclass
class FeatureLineage:
    """피처 계보 (어떤 데이터에서 계산됐는지)"""
    ticker: str
    computed_at: str
    ohlcv_rows: int
    indicators: List[str]
    cache_hit: bool = False

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "computed_at": self.computed_at,
            "ohlcv_rows": self.ohlcv_rows,
            "indicators": self.indicators,
            "cache_hit": self.cache_hit,
        }


# ═══════════════════════════════════════════════════════════════════
#  FeatureValidator
# ═══════════════════════════════════════════════════════════════════

_FEATURE_RANGES: Dict[str, Tuple[float, float]] = {
    "rsi":          (0.0,   100.0),
    "pct_b":        (0.0,   1.0),
    "pct_k":        (0.0,   100.0),
    "pct_d":        (0.0,   100.0),
    "volume_ratio": (0.0,   100.0),
    "atr":          (0.0,   1e9),
    "ema5":         (0.0,   1e9),
    "ema20":        (0.0,   1e9),
    "ema60":        (0.0,   1e9),
}


class FeatureValidator:
    """피처 범위 검증 및 NaN/Inf 탐지"""

    @staticmethod
    def validate(features: Dict[str, Any]) -> FeatureValidationResult:
        invalid: List[str] = []
        warnings: List[str] = []

        for name, value in features.items():
            if not isinstance(value, (int, float)):
                continue
            if math.isnan(value) or math.isinf(value):
                invalid.append(name)
                warnings.append(f"{name}: NaN 또는 Inf 값 ({value})")
                continue
            if name in _FEATURE_RANGES:
                lo, hi = _FEATURE_RANGES[name]
                if not (lo <= value <= hi):
                    invalid.append(name)
                    warnings.append(f"{name}: 범위 초과 ({value}, 허용 {lo}~{hi})")

        return FeatureValidationResult(
            is_valid=len(invalid) == 0,
            invalid_features=invalid,
            warnings=warnings,
        )


# ═══════════════════════════════════════════════════════════════════
#  FeatureStore (v2.0)
# ═══════════════════════════════════════════════════════════════════

class FeatureStore:
    """TTL 기반 피처 캐시 + OHLCV 기술지표 파이프라인.

    기존 analytics/daily_monitor.py의 `await self.feature_store.get_stats()`
    호출 패턴과 완전히 호환됩니다 (fresh_rate 키는 실제 캐시 적중률 기반 계산).
    """

    def __init__(self, ttl: int = _DEFAULT_TTL, maxsize: int = _DEFAULT_MAXSIZE) -> None:
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lineage: Dict[str, FeatureLineage] = {}
        self._validator = FeatureValidator()

    async def compute_all_features(
        self, ticker: str, ohlcv_data: List[Dict[str, Any]]
    ) -> Dict[str, float]:
        cache_key = f"features:{ticker}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if ticker in self._lineage:
                lin = self._lineage[ticker]
                self._lineage[ticker] = FeatureLineage(
                    ticker=lin.ticker, computed_at=lin.computed_at,
                    ohlcv_rows=lin.ohlcv_rows, indicators=lin.indicators,
                    cache_hit=True,
                )
            return cached

        features = await self._compute(ticker, ohlcv_data)
        self._cache[cache_key] = features
        return features

    async def _compute(
        self, ticker: str, ohlcv_data: List[Dict[str, Any]]
    ) -> Dict[str, float]:
        if len(ohlcv_data) < _MIN_OHLCV_ROWS:
            return {}

        closes = [float(d.get("close", 0)) for d in ohlcv_data]
        highs  = [float(d.get("high",  d.get("close", 0))) for d in ohlcv_data]
        lows   = [float(d.get("low",   d.get("close", 0))) for d in ohlcv_data]
        vols   = [float(d.get("volume", 0)) for d in ohlcv_data]

        ema5  = compute_ema(closes, 5)
        ema20 = compute_ema(closes, 20)
        ema60 = compute_ema(closes, min(60, len(closes)))
        rsi = compute_rsi(closes, 14)
        bb_upper, bb_middle, bb_lower, pct_b = compute_bollinger(closes, 20, 2.0)
        macd_line, macd_signal, macd_hist = compute_macd(closes, 12, 26, 9)
        atr = compute_atr(highs, lows, closes, 14)
        pct_k, pct_d = compute_stochastic(highs, lows, closes, 14, 3)

        non_zero_vols = [v for v in vols if v > 0]
        avg_vol = sum(non_zero_vols) / len(non_zero_vols) if non_zero_vols else 1.0
        current_vol = vols[-1] if vols else 0.0
        volume_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

        features: Dict[str, float] = {
            "ema5":         round(ema5, 4),
            "ema20":        round(ema20, 4),
            "ema60":        round(ema60, 4),
            "rsi":          round(rsi, 4),
            "bb_upper":     round(bb_upper, 4),
            "bb_middle":    round(bb_middle, 4),
            "bb_lower":     round(bb_lower, 4),
            "pct_b":        round(pct_b, 4),
            "macd":         round(macd_line, 6),
            "macd_signal":  round(macd_signal, 6),
            "macd_hist":    round(macd_hist, 6),
            "atr":          round(atr, 4),
            "pct_k":        round(pct_k, 4),
            "pct_d":        round(pct_d, 4),
            "volume_ratio": round(volume_ratio, 4),
            "current_price": round(closes[-1], 4),
        }

        self._lineage[ticker] = FeatureLineage(
            ticker=ticker,
            computed_at=datetime.now().isoformat(),
            ohlcv_rows=len(ohlcv_data),
            indicators=list(features.keys()),
            cache_hit=False,
        )
        return features

    async def validate_features(self, features: Dict[str, Any]) -> FeatureValidationResult:
        return self._validator.validate(features)

    def get_lineage(self, ticker: str) -> Optional[FeatureLineage]:
        return self._lineage.get(ticker)

    def get_lineage_report(self, ticker: str) -> Dict[str, Any]:
        lin = self._lineage.get(ticker)
        if lin is None:
            return {"error": f"{ticker} lineage not found"}
        return lin.to_dict()

    async def get_stats(self) -> Dict[str, Any]:
        """analytics/daily_monitor.py의 fresh_rate 판정과 호환되는 실계산 통계."""
        total = len(self._lineage)
        cached = sum(1 for lin in self._lineage.values() if lin.cache_hit)
        fresh_rate = cached / total if total > 0 else 0.0
        return {
            "total_tickers": total,
            "cache_size": len(self._cache),
            "cache_maxsize": self._cache.maxsize,
            "fresh_rate": round(fresh_rate, 4),
        }

    def invalidate(self, ticker: str) -> bool:
        key = f"features:{ticker}"
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self) -> None:
        self._cache.clear()
        self._lineage.clear()


# ═══════════════════════════════════════════════════════════════════
#  BatchFeatureComputer
# ═══════════════════════════════════════════════════════════════════

class BatchFeatureComputer:
    """여러 종목의 피처를 asyncio.gather로 동시 계산."""

    def __init__(self, store: FeatureStore) -> None:
        self._store = store

    async def compute_batch(
        self, ticker_ohlcv: Dict[str, List[Dict[str, Any]]]
    ) -> Dict[str, Dict[str, float]]:
        if not ticker_ohlcv:
            return {}
        tickers = list(ticker_ohlcv.keys())
        tasks = [self._store.compute_all_features(t, ticker_ohlcv[t]) for t in tickers]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        results: Dict[str, Dict[str, float]] = {}
        for ticker, result in zip(tickers, results_list):
            if isinstance(result, Exception):
                logger.warning(f"BatchFeatureComputer: {ticker} 계산 실패: {result}")
                results[ticker] = {}
            else:
                results[ticker] = result
        return results

    async def validate_batch(
        self, batch_features: Dict[str, Dict[str, float]]
    ) -> Dict[str, FeatureValidationResult]:
        results: Dict[str, FeatureValidationResult] = {}
        for ticker, features in batch_features.items():
            results[ticker] = await self._store.validate_features(features)
        return results
