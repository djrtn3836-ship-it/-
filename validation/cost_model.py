# -*- coding: utf-8 -*-
"""유동성 기반 체결비용 모델 (S152).

가정(명시):
- 호가 스프레드 = tick_size × spread_ticks (기본 2틱), 편도 절반만 부담
- 시장충격 = IMPACT_K × sqrt(참여율%)  [제곱근 충격 법칙, %]
- 수수료 편도 1.5bp, 매도 거래세 15bp(2026 기준 가정)
"""
from __future__ import annotations

import bisect
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

COMMISSION_BP = 1.5
TAX_SELL_BP = 15.0
IMPACT_K = 0.10
SPREAD_TICKS = 2.0


def tick_size(price: float) -> float:
    if price < 2000:
        return 1.0
    if price < 5000:
        return 5.0
    if price < 20000:
        return 10.0
    if price < 50000:
        return 50.0
    if price < 200000:
        return 100.0
    if price < 500000:
        return 500.0
    return 1000.0


def spread_bp(price: float, ticks: float = SPREAD_TICKS) -> float:
    """왕복 스프레드 비용(bp) = 틱×ticks/가격."""
    if price <= 0:
        return 0.0
    return ticks * tick_size(price) / price * 10000.0


def impact_bp(participation_pct: float, k: float = IMPACT_K) -> float:
    return k * math.sqrt(max(participation_pct, 0.0)) * 100.0


def _close_on(series: Dict[str, Any], ticker: str, day: str) -> Optional[float]:
    entry = series.get(ticker)
    if not entry:
        return None
    dates, closes = entry[0], entry[2]
    i = bisect.bisect_right(dates, day) - 1
    return closes[i] if i >= 0 else None


def adv_krw(series: Dict[str, Any], ticker: str, day: str, window: int = 20) -> Optional[float]:
    entry = series.get(ticker)
    if not entry:
        return None
    dates, closes, vols = entry[0], entry[2], entry[3]
    i = bisect.bisect_right(dates, day) - 1
    if i < 0:
        return None
    lo = max(0, i - window + 1)
    vals = [closes[k] * vols[k] for k in range(lo, i + 1) if vols[k] > 0]
    return statistics.mean(vals) if vals else None


class LiquidityCost:
    """자본 규모를 반영한 체결비용 콜러블(simulate(cost_fn=...))."""

    def __init__(
        self,
        series: Dict[str, Any],
        capital: float,
        *,
        spread_ticks: float = SPREAD_TICKS,
        impact_k: float = IMPACT_K,
        commission_bp: float = COMMISSION_BP,
        tax_bp: float = TAX_SELL_BP,
    ):
        self.series = series
        self.capital = float(capital)
        self.spread_ticks = spread_ticks
        self.impact_k = impact_k
        self.commission_bp = commission_bp
        self.tax_bp = tax_bp
        self.samples: List[float] = []
        self.participations: List[float] = []

    def cost(self, ticker: str, day: str, notional_units: float, side: str) -> float:
        px = _close_on(self.series, ticker, day) or 0.0
        adv = adv_krw(self.series, ticker, day)
        part = 0.0
        if px > 0 and adv and adv > 0:
            part = 100.0 * (float(notional_units) * self.capital) / adv
        c_bp = (
            self.commission_bp
            + spread_bp(px, self.spread_ticks) / 2.0
            + impact_bp(part, self.impact_k)
        )
        if side == "sell":
            c_bp += self.tax_bp
        self.samples.append(c_bp)
        self.participations.append(part)
        return c_bp / 10000.0

    def summary(self) -> Dict[str, Any]:
        if not self.samples:
            return {"n": 0}
        return {
            "n": len(self.samples),
            "avg_bp": round(statistics.mean(self.samples), 1),
            "median_bp": round(statistics.median(self.samples), 1),
            "p90_bp": round(sorted(self.samples)[int(len(self.samples) * 0.9) - 1], 1),
            "avg_part_pct": round(statistics.mean(self.participations), 2),
            "p90_part_pct": round(sorted(self.participations)[int(len(self.participations) * 0.9) - 1], 2),
        }


def capacity_stats(
    series: Dict[str, Any],
    signals: List[Dict[str, Any]],
    *,
    max_part_pct: float = 1.0,
    max_positions: int = 5,
) -> Dict[str, Any]:
    """신호별 1% ADV 기준 수용 가능 금액(1회 포지션) 통계."""
    caps = []
    for s in signals:
        adv = adv_krw(series, s["ticker"], s["entry_date"])
        if adv:
            caps.append(adv * max_part_pct / 100.0)
    if not caps:
        return {}
    caps.sort()
    return {
        "n": len(caps),
        "median_krw": round(statistics.median(caps)),
        "p10_krw": round(caps[int(len(caps) * 0.1)]),
        "p90_krw": round(caps[int(len(caps) * 0.9)]),
        "portfolio_krw": round(statistics.median(caps) * max_positions),
    }
