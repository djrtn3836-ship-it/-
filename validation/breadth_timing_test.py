# -*- coding: utf-8 -*-
"""validation/breadth_timing_test.py — 시장 폭의 레짐 가치 검증 (2026-10-19).

배경:
    S130에서 브리핑에 '시장 폭(MA20 상회 비율·상승/하락·신고/신저)'을 추가했다.
    이 지표가 **시장 레벨 타이밍 정보**를 주는지 5년 역사로 검증한다.
    (개별주 횡단면이 아니라 KOSPI 지수 전방수익 기준 — 다른 메커니즘의 검정)

방법:
    - 보유 195종목 일봉으로 **일별 폭 역사**를 계산(그 시점까지의 데이터만 사용).
    - 조건(이벤트): 폭 극단 발생 **첫날만** 기록(연속 중복 제거 — S128 교훈).
        L1: MA20 상회 ≤ 20% (패닉)
        L2: MA20 상회 ≤ 10% (극단 패닉)
        H1: MA20 상회 ≥ 80% (과열)
    - 성과: KOSPI(^KS11) 전방 5일/20일 수익률 vs **무조건 평균(기준선)**.
    - 연도별 일관성 + 적중률 + t값.
사용:
    python -m validation.breadth_timing_test
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from core.logger import setup_logger

logger = setup_logger("breadth_timing_test")

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "decisions.db"
HISTORY_OUT = ROOT / "data" / "breadth_history.json"

CONDITIONS = {
    "L1_폭20이하": lambda p: p is not None and p <= 20.0,
    "L2_폭10이하": lambda p: p is not None and p <= 10.0,
    "H1_폭80이상": lambda p: p is not None and p >= 80.0,
}
HORIZONS = (5, 20)


@dataclass
class S:
    dates: List[str]
    close: List[float]
    high: List[float]
    low: List[float]


def load_universe(db_path: Path) -> Dict[str, S]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out: Dict[str, S] = {}
        cur = con.execute(
            "SELECT ticker, date, close, high, low FROM ohlcv "
            "WHERE ticker NOT LIKE '^%' ORDER BY ticker, date"
        )
        for t, d, c, h, lo in cur:
            if not c or float(c) <= 0:
                continue
            s = out.setdefault(t, S([], [], [], []))
            s.dates.append(str(d))
            s.close.append(float(c))
            s.high.append(float(h or c))
            s.low.append(float(lo or c))
        return {k: v for k, v in out.items() if len(v.dates) >= 30}
    finally:
        con.close()


def load_index(db_path: Path, ticker: str = "^KS11") -> S:
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


def breadth_at(series: Dict[str, S], asof: str, *, ma_w: int = 20, max_age: int = 7) -> Optional[float]:
    """asof 시점의 'MA20 상회 비율(%). 데이터 부족 시 None."""
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


def fwd_return(closes: Sequence[float], i: int, h: int) -> Optional[float]:
    j = i + h
    if j >= len(closes) or closes[i] <= 0 or closes[j] <= 0:
        return None
    return closes[j] / closes[i] - 1.0


def _tstat(vals: Sequence[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    sd = statistics.stdev(vals)
    return statistics.fmean(vals) / (sd / math.sqrt(n)) if sd else 0.0


def latest_breadth(breadths: Dict[str, float]) -> Optional[float]:
    """최신 날짜의 폭 값(정렬 최댓값 아님 — 2026-10-19 버그 수정)."""
    if not breadths:
        return None
    return breadths[max(breadths)]


def find_events(breadths: Dict[str, float]) -> Dict[str, List[str]]:
    """조건별 '켜지는 첫날' 날짜 목록(연속 중복 제거)."""
    dates = sorted(breadths)
    events: Dict[str, List[str]] = {c: [] for c in CONDITIONS}
    prev = {c: False for c in CONDITIONS}
    for d in dates:
        p = breadths[d]
        for c, fn in CONDITIONS.items():
            now = bool(fn(p))
            if now and not prev[c]:
                events[c].append(d)
            prev[c] = now
    return events


def evaluate(index: S, events: Dict[str, List[str]], h: int) -> Dict[str, dict]:
    pos = {d: i for i, d in enumerate(index.dates)}
    base = [r for i in range(len(index.close)) if (r := fwd_return(index.close, i, h)) is not None]
    base_mean = statistics.fmean(base) if base else 0.0
    out: Dict[str, dict] = {}
    for c, dates in events.items():
        rets = []
        yearly: Dict[str, List[float]] = defaultdict(list)
        for d in dates:
            i = pos.get(d)
            if i is None:
                continue
            r = fwd_return(index.close, i, h)
            if r is None:
                continue
            rets.append(r - base_mean)
            yearly[d[:4]].append(r - base_mean)
        if not rets:
            out[c] = {"n": 0}
            continue
        ym = {y: statistics.fmean(v) * 100 for y, v in sorted(yearly.items())}
        out[c] = {
            "n": len(rets),
            "excess": statistics.fmean(rets) * 100.0,
            "hit": sum(1 for x in rets if x > 0) / len(rets) * 100.0,
            "t": _tstat(rets),
            "years": ym,
        }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="시장 폭 레짐 가치 검증")
    ap.add_argument("--db", type=str, default=str(DB_PATH))
    ap.add_argument("--save-history", action="store_true", help="일별 폭 역사 JSON 저장")
    args = ap.parse_args(argv)

    db = Path(args.db)
    series = load_universe(db)
    index = load_index(db)
    if not series or not index.dates:
        logger.error("데이터 없음 — DB 확인")
        return 1

    logger.info("일별 폭 역사 계산 중...")
    breadths: Dict[str, float] = {}
    for d in index.dates:
        p = breadth_at(series, d)
        if p is not None:
            breadths[d] = round(p, 2)
    if len(breadths) < 100:
        logger.error(f"폭 역사 부족: {len(breadths)}일")
        return 1
    if args.save_history:
        HISTORY_OUT.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_OUT.write_text(
            json.dumps({"updated_at": date.today().isoformat(), "dates": breadths},
                       ensure_ascii=True, indent=0),
            encoding="utf-8",
        )
        logger.info(f"폭 역사 저장: {HISTORY_OUT} ({len(breadths)}일)")

    events = find_events(breadths)
    print(f"■ 시장 폭 레짐 검증 — 폭 역사 {len(breadths)}일 · 이벤트(첫날 기준)")
    for c in CONDITIONS:
        print(f"  {c}: {len(events[c])}회", end="")
    print()
    for h in HORIZONS:
        print(f"\n■ KOSPI 전방 {h}일 (기준선 대비 초과, %)")
        stats = evaluate(index, events, h)
        for c, st in stats.items():
            if st.get("n", 0) == 0:
                continue
            years = " ".join(f"{y[2:]}:{v:+.1f}" for y, v in st["years"].items())
            print(f"  {c:<12} n={st['n']:>3} 초과 {st['excess']:+.2f}%p "
                  f"적중 {st['hit']:.0f}% t={st['t']:+.2f}  {years}")
    # 현재 위치(백분위)
    cur = latest_breadth(breadths)
    if cur is not None:
        vals = sorted(breadths.values())
        pct = bisect.bisect_right(vals, cur) / len(vals) * 100.0
        print(f"\n현재(최신) 폭: {cur:.0f}% — 역사 백분위 {pct:.0f}%")
    print("\n판정: |t|>2 + 연도 일관성(플러스 연도 3/4+)이면 레짐 신호 후보.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
