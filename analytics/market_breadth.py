# -*- coding: utf-8 -*-
"""analytics/market_breadth.py — 시장 폭(자체 유니버스) 계산 (2026-10-19).

배경:
    정보 커버리지 감사에서 '시장 폭(상승/하락 종목 수, 이평 상회 비율)' 부재 확인.
    외부 유료 소스 없이 보유 일봉으로 계산 가능 → 브리핑에 시장 맥락 제공.
    ※ 판단 점수는 불변(표시용) — 검증 전에는 행동에 반영하지 않는다.

정의(최신 거래일 기준):
    - MA20/MA60 상회 비율: 종가 > 단순이동평균
    - 상승/하락: 전일 대비 종가 부호
    - 20일 신고/신저: 당일 고가 ≥ 직전 20일 최고(신고) / 저가 ≤ 최저(신저)
    - 신선도: 마지막 봉이 기준일로부터 max_age_days 이내인 종목만 집계(정지/누락 배제)
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("market_breadth")

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "decisions.db"

Series = Dict[str, Dict[str, List[float]]]


def load_breadth_series(
    db_path: Path = DB_PATH, lookback_days: int = 100
) -> Series:
    """ohlcv에서 종목별 (dates, close, high, low) 시계열 적재(최근 lookback_days)."""
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out: Series = {}
        cur = con.execute(
            "SELECT ticker, date, close, high, low FROM ohlcv "
            "WHERE ticker NOT LIKE '^%' AND date >= ? ORDER BY ticker, date",
            (cutoff,),
        )
        for t, d, c, h, lo in cur:
            if not c or float(c) <= 0:
                continue
            s = out.setdefault(t, {"dates": [], "close": [], "high": [], "low": []})
            s["dates"].append(str(d))
            s["close"].append(float(c))
            s["high"].append(float(h or c))
            s["low"].append(float(lo or c))
        return out
    finally:
        con.close()


def _count_recent(ref: str, last: str, max_age_days: int) -> bool:
    try:
        return (date.fromisoformat(ref) - date.fromisoformat(last)).days <= max_age_days
    except ValueError:
        return False


def compute_breadth(
    series: Series,
    *,
    max_age_days: int = 7,
    ma_short: int = 20,
    ma_long: int = 60,
    breakout_window: int = 20,
) -> Dict[str, object]:
    """시장 폭 계산. 최신 기준일 기준 집계 dict 반환(데이터 없으면 n=0)."""
    ref = ""
    for s in series.values():
        if s["dates"] and s["dates"][-1] > ref:
            ref = s["dates"][-1]
    if not ref:
        return {"n": 0, "ref_date": ""}

    n = adv = dec = unch = 0
    above_s = above_l = 0
    n_short = n_long = 0
    new_high = new_low = 0
    n_break = 0

    for s in series.values():
        dates = s["dates"]
        closes = s["close"]
        if not dates or not _count_recent(ref, dates[-1], max_age_days):
            continue
        n += 1
        if len(closes) >= 2:
            if closes[-1] > closes[-2]:
                adv += 1
            elif closes[-1] < closes[-2]:
                dec += 1
            else:
                unch += 1
        if len(closes) >= ma_short:
            n_short += 1
            if closes[-1] > sum(closes[-ma_short:]) / ma_short:
                above_s += 1
        if len(closes) >= ma_long:
            n_long += 1
            if closes[-1] > sum(closes[-ma_long:]) / ma_long:
                above_l += 1
        highs = s["high"]
        lows = s["low"]
        if len(highs) >= breakout_window + 1:
            n_break += 1
            if highs[-1] >= max(highs[-(breakout_window + 1):]):
                new_high += 1
            if lows[-1] <= min(lows[-(breakout_window + 1):]):
                new_low += 1

    def _pct(a: int, b: int) -> Optional[float]:
        return round(a / b * 100.0, 1) if b else None

    return {
        "ref_date": ref,
        "n": n,
        "above_ma20_pct": _pct(above_s, n_short),
        "above_ma60_pct": _pct(above_l, n_long),
        "advancers": adv,
        "decliners": dec,
        "unchanged": unch,
        "new_highs": new_high,
        "new_lows": new_low,
        "breakout_n": n_break,
    }


def format_breadth(b: Dict[str, object]) -> Optional[str]:
    """브리핑용 한 줄. 표본 부족 시 None(추정 금지)."""
    if not b or int(b.get("n") or 0) < 20:
        return None
    parts = []
    if b.get("above_ma20_pct") is not None:
        parts.append(f"MA20↑ {b['above_ma20_pct']:.0f}%")
    if b.get("above_ma60_pct") is not None:
        parts.append(f"MA60↑ {b['above_ma60_pct']:.0f}%")
    parts.append(f"상승/하락 {b.get('advancers', 0)}/{b.get('decliners', 0)}")
    parts.append(f"20일 신고 {b.get('new_highs', 0)}/신저 {b.get('new_lows', 0)}")
    return "📐 폭: " + " · ".join(parts)
