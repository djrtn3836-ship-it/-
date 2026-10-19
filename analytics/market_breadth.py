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

import bisect
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from core.logger import setup_logger

logger = setup_logger("market_breadth")

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "decisions.db"
HISTORY_PATH = ROOT / "data" / "breadth_history.json"

# S132 검증(validation/breadth_timing_test.py --robust): 폭 ≤15% 첫날 18회 →
# 이후 5일 KOSPI +5.1%p·반등 78%·t=3.46 (순열 p<0.001) — 단 2022 약세장은 무반등.
# 맥락 표시 전용(판단/점수 불변).
PANIC_THRESHOLD = 15.0
PANIC_NOTE = "배경: 과거 18회 유사일 후 5일 +5.1%p(78%) · 2022년엔 무효 — 참고 맥락"

Series = Dict[str, Dict[str, List[float]]]


@dataclass
class S:
    """종목 1개의 전체 일봉 시계열(폭 역사 계산용 — S131 검증도구에서 이관).

    open/volume은 S136(매도규칙 검증)에서 추가 — 기본값으로 기존 호출과 호환.
    """

    dates: List[str]
    close: List[float]
    high: List[float]
    low: List[float]
    open: List[float] = field(default_factory=list)
    volume: List[float] = field(default_factory=list)


def load_universe(db_path: Path = DB_PATH, min_days: int = 30, with_open_volume: bool = False) -> Dict[str, S]:
    """전 종목(비지수) 일봉 전체 시계열 — 일별 폭 역사 계산용.

    with_open_volume=True면 시가·거래량도 적재(S136 매도규칙 검증).
    """
    cols = "ticker, date, close, high, low"
    if with_open_volume:
        cols += ", open, volume"
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out: Dict[str, S] = {}
        cur = con.execute(
            f"SELECT {cols} FROM ohlcv WHERE ticker NOT LIKE '^%' ORDER BY ticker, date"
        )
        for row in cur:
            t, d, c, h, lo = row[0], row[1], row[2], row[3], row[4]
            if not c or float(c) <= 0:
                continue
            s = out.setdefault(t, S([], [], [], []))
            s.dates.append(str(d))
            s.close.append(float(c))
            s.high.append(float(h or c))
            s.low.append(float(lo or c))
            if with_open_volume:
                s.open.append(float(row[5] or c))
                s.volume.append(float(row[6] or 0.0))
        return {k: v for k, v in out.items() if len(v.dates) >= min_days}
    finally:
        con.close()


def load_index(db_path: Path = DB_PATH, ticker: str = "^KS11") -> S:
    """지수 일봉 시계열(기본 KOSPI — 레짐 검증/폭 역사 기준일용)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        s = S([], [], [], [])
        for d, c in con.execute(
            "SELECT date, close FROM ohlcv WHERE ticker=? ORDER BY date", (ticker,)
        ):
            if c and float(c) > 0:
                s.dates.append(str(d))
                s.close.append(float(c))
        return s
    finally:
        con.close()


def _idx_le(dates: Sequence[str], asof: str) -> int:
    return bisect.bisect_right(dates, asof) - 1


def breadth_at(
    series: Dict[str, S], asof: str, *, ma_w: int = 20, max_age: int = 7
) -> Optional[float]:
    """asof 시점의 'MA20 상회 비율(%)' — 그 시점까지의 데이터만 사용(룩어헤드 없음)."""
    n = above = 0
    try:
        asof_d = date.fromisoformat(asof)
    except ValueError:
        return None
    for s in series.values():
        i = _idx_le(s.dates, asof)
        if i < 0:
            continue
        try:
            if (asof_d - date.fromisoformat(s.dates[i])).days > max_age:
                continue
        except ValueError:
            continue
        if i + 1 < ma_w:
            continue
        n += 1
        ma = sum(s.close[i + 1 - ma_w: i + 1]) / ma_w
        if s.close[i] > ma:
            above += 1
    if n < 20:
        return None
    return above / n * 100.0


def save_history(breadths: Dict[str, float], out_path: Optional[Path] = None) -> int:
    """일별 폭 역사 JSON 저장(검증도구·주말 잡 공용). 반환: 저장 일수."""
    out = Path(out_path or HISTORY_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"updated_at": date.today().isoformat(), "dates": breadths},
            ensure_ascii=True,
            indent=0,
        ),
        encoding="utf-8",
    )
    return len(breadths)


def refresh_history(db_path: Optional[Path] = None, out_path: Optional[Path] = None) -> int:
    """일별 폭 역사를 전 종목 일봉으로 재계산·저장(주말 유지보수 잡). 반환: 일수.

    100일 미만이면 오류 — 백분위 표시가 낡은 파일로 남는 것을 방지.
    """
    db = Path(db_path or DB_PATH)
    series = load_universe(db)
    index = load_index(db)
    if not series or not index.dates:
        raise RuntimeError("ohlcv 데이터 없음 — 폭 역사 갱신 불가")
    breadths: Dict[str, float] = {}
    for d in index.dates:
        p = breadth_at(series, d)
        if p is not None:
            breadths[d] = round(p, 2)
    if len(breadths) < 100:
        raise RuntimeError(f"폭 역사 부족: {len(breadths)}일")
    n = save_history(breadths, out_path)
    logger.info(f"폭 역사 갱신 완료: {out_path or HISTORY_PATH} ({n}일)")
    return n


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


def load_breadth_history(path: Path = HISTORY_PATH) -> Dict[str, float]:
    """일별 폭 역사 JSON 로드(validation 도구가 생성). 없거나 손상이면 {} (표시 생략)."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        dates = raw.get("dates", {})
        return {str(d): float(v) for d, v in dates.items()}
    except Exception:
        return {}


def breadth_percentile(history: Dict[str, float], value: Optional[float]) -> Optional[float]:
    """현재 폭이 역사 분포에서 몇 백분위인가(0~100)."""
    if not history or value is None:
        return None
    vals = sorted(history.values())
    return bisect.bisect_right(vals, value) / len(vals) * 100.0


def format_breadth(
    b: Dict[str, object], history: Optional[Dict[str, float]] = None
) -> Optional[str]:
    """브리핑용 한 줄. 표본 부족 시 None(추정 금지). history 주면 백분위 병기."""
    if not b or int(b.get("n") or 0) < 20:
        return None
    parts = []
    if b.get("above_ma20_pct") is not None:
        pct_txt = f"MA20↑ {b['above_ma20_pct']:.0f}%"
        b_pct = breadth_percentile(history or {}, float(b["above_ma20_pct"]))
        if b_pct is not None:
            pct_txt += f" (역사 백분위 {b_pct:.0f}%)"
        parts.append(pct_txt)
    if b.get("above_ma60_pct") is not None:
        parts.append(f"MA60↑ {b['above_ma60_pct']:.0f}%")
    parts.append(f"상승/하락 {b.get('advancers', 0)}/{b.get('decliners', 0)}")
    parts.append(f"20일 신고 {b.get('new_highs', 0)}/신저 {b.get('new_lows', 0)}")
    return "📐 폭: " + " · ".join(parts)
