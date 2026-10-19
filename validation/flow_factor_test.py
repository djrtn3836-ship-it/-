# -*- coding: utf-8 -*-
"""validation/flow_factor_test.py — 수급 팩터 예측력 검증 (2026-10-19).

배경:
    수급 5년 백필 완료(228,730행) → flow 태그(쌍끌이/연속/다이버전스)의 전제를
    실제 데이터로 판정한다. "추측 금지" 원칙 — 유의성이 없으면 태그는 기록용 유지.

방법 (사건 연구 + 시장 베타 제거):
    - 각 거래일, 조건을 충족하는 (종목)의 이벤트를 모은다.
    - 초과수익 = 그 종목의 전방수익 − 같은 날 유니버스 평균 전방수익(5일/20일).
    - 지표: 이벤트 수 · 평균 초과(%p) · 적중률 · t값 · 연도별 일관성.
조건:
    A 쌍끌이    : 외국인 5일합 > 0 AND 기관 5일합 > 0
    B 외국인연속: 외국인 3일 연속 순매수(당일 포함)
    C 매집다이버: 주가 5일수익 ≤ -2% AND 외국인 5일합 > 0
    D (대조)    : 외국인 5일합 > 0
사용:
    python -m validation.flow_factor_test --start 2021-10-18 --end 2026-10-16
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from core.logger import setup_logger

logger = setup_logger("flow_factor_test")

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "decisions.db"
HORIZONS: Tuple[int, ...] = (5, 20)

CONDITIONS = ("A_쌍끌이", "B_외국인연속3", "C_매집다이버", "D_외국인매수")


@dataclass
class Series:
    """종목별 정렬된 일봉+수급 시계열(날짜 교집합, 오름차순)."""

    dates: List[str] = field(default_factory=list)
    close: List[float] = field(default_factory=list)
    volume: List[float] = field(default_factory=list)
    foreign: List[float] = field(default_factory=list)
    inst: List[float] = field(default_factory=list)


def _fwd_return(closes: Sequence[float], idx: int, horizon: int) -> Optional[float]:
    j = idx + horizon
    if j >= len(closes) or closes[idx] <= 0 or closes[j] <= 0:
        return None
    return closes[j] / closes[idx] - 1.0


def _streak_buy(vals: Sequence[float], idx: int, length: int = 3) -> bool:
    if idx + 1 < length:
        return False
    return all(vals[k] > 0 for k in range(idx - length + 1, idx + 1))


def _back_return(closes: Sequence[float], idx: int, lookback: int) -> Optional[float]:
    j = idx - lookback
    if j < 0 or closes[j] <= 0 or closes[idx] <= 0:
        return None
    return closes[idx] / closes[j] - 1.0


def collect_events(series: Dict[str, Series], dedupe_runs: bool = True) -> Dict[str, List[dict]]:
    """조건별 이벤트 수집. 이벤트 = {date, ticker, fr5, fr20}.

    dedupe_runs=True(기본): 조건이 **켜지는 날**(전일 꺼짐→오늘 켜짐)만 기록.
    연속 지속일 중복으로 t값이 과대평가되는 것을 막는다(진입 시나리오와도 일치).
    """
    events: Dict[str, List[dict]] = {c: [] for c in CONDITIONS}
    for ticker, s in series.items():
        n = len(s.dates)
        f = s.foreign
        i = s.inst
        closes = s.close
        prev = {c: False for c in CONDITIONS}
        for d in range(5, n):  # 5일 창 필요
            f5 = sum(f[d - 4: d + 1])
            i5 = sum(i[d - 4: d + 1])
            r5b = _back_return(closes, d, 5)
            now = {
                "A_쌍끌이": f5 > 0 and i5 > 0,
                "B_외국인연속3": _streak_buy(f, d, 3),
                "C_매집다이버": r5b is not None and r5b <= -0.02 and f5 > 0,
                "D_외국인매수": f5 > 0,
            }
            fr5 = _fwd_return(closes, d, 5)
            fr20 = _fwd_return(closes, d, 20)
            if fr5 is None and fr20 is None:
                prev = now
                continue
            ev = {"date": s.dates[d], "ticker": ticker, "fr5": fr5, "fr20": fr20}
            for c in CONDITIONS:
                if now[c] and not (dedupe_runs and prev[c]):
                    events[c].append(dict(ev))
            prev = now
    return events


def universe_means(series: Dict[str, Series], horizon: int) -> Dict[str, float]:
    """날짜별 유니버스 평균 전방수익 (베타 제거 기준선)."""
    acc: Dict[str, List[float]] = defaultdict(list)
    for s in series.values():
        closes = s.close
        for d in range(len(closes)):
            fr = _fwd_return(closes, d, horizon)
            if fr is not None:
                acc[s.dates[d]].append(fr)
    return {k: sum(v) / len(v) for k, v in acc.items() if v}


def _tstat(vals: Sequence[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    sd = statistics.stdev(vals)
    if sd == 0:
        return 0.0
    return statistics.fmean(vals) / (sd / math.sqrt(n))


def summarize(events: List[dict], means: Dict[str, float], horizon: int) -> dict:
    """초과수익 통계 + 연도별 일관성."""
    key = f"fr{horizon}"
    excess = [ev[key] - means[ev["date"]] for ev in events
              if ev.get(key) is not None and ev["date"] in means]
    if not excess:
        return {"n": 0}
    yearly: Dict[str, List[float]] = defaultdict(list)
    for ev in events:
        if ev.get(key) is None or ev["date"] not in means:
            continue
        yearly[ev["date"][:4]].append(ev[key] - means[ev["date"]])
    year_means = {y: sum(v) / len(v) for y, v in sorted(yearly.items())}
    pos_years = sum(1 for v in year_means.values() if v > 0)
    return {
        "n": len(excess),
        "mean": statistics.fmean(excess) * 100.0,
        "median": statistics.median(excess) * 100.0,
        "hit": sum(1 for x in excess if x > 0) / len(excess) * 100.0,
        "t": _tstat(excess),
        "years": year_means,
        "pos_years": pos_years,
        "n_years": len(year_means),
    }


def load_series(db_path: Path, start: str, end: str, limit: int = 200) -> Dict[str, Series]:
    """DB에서 수급 보유 종목의 일봉+수급 시계열을 날짜 교집합으로 적재."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tickers = [r[0] for r in con.execute(
            "SELECT ticker FROM supply_demand WHERE date >= ? GROUP BY ticker "
            "ORDER BY COUNT(*) DESC LIMIT ?", (start, limit),
        )]
        out: Dict[str, Series] = {}
        for t in tickers:
            ox = {r[0]: (float(r[1]), float(r[2] or 0)) for r in con.execute(
                "SELECT date, close, volume FROM ohlcv WHERE ticker=? AND date>=? AND date<=?",
                (t, start, end),
            )}
            sd = list(con.execute(
                "SELECT date, foreign_net, institution_net FROM supply_demand "
                "WHERE ticker=? AND date>=? AND date<=? ORDER BY date",
                (t, start, end),
            ))
            s = Series()
            for d, f, i in sd:
                if d not in ox:
                    continue
                c, v = ox[d]
                if c <= 0:
                    continue
                s.dates.append(d)
                s.close.append(c)
                s.volume.append(v)
                s.foreign.append(float(f or 0.0))
                s.inst.append(float(i or 0.0))
            if len(s.dates) >= 30:  # 최소 표본
                out[t] = s
        return out
    finally:
        con.close()


def run_scan(series: Dict[str, Series]) -> None:
    events = collect_events(series)
    print(f"■ 수급 팩터 검증 — 유니버스 {len(series)}종목")
    print(f"{'조건':<14}{'지평':>4}{'이벤트':>8}{'평균초과':>9}{'적중률':>8}{'t값':>7}  연도별(평균초과 %p)")
    means_cache = {h: universe_means(series, h) for h in HORIZONS}
    for cond in CONDITIONS:
        for h in HORIZONS:
            st = summarize(events[cond], means_cache[h], h)
            if st["n"] == 0:
                continue
            years = " ".join(f"{y[2:]}:{v * 100:+.1f}" for y, v in st["years"].items())
            print(
                f"{cond:<14}{h:>4}{st['n']:>8}{st['mean']:>+9.2f}"
                f"{st['hit']:>7.1f}%{st['t']:>+7.2f}  {years}"
            )
        print()
    print("판정 기준: |t|>2 그리고 연도 일관성(플러스 연도 ≥ 70%).")
    print("초과수익 = 개별 전방수익 − 같은 날 유니버스 평균(시장 방향 제거).")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="수급 팩터 예측력 검증(사건 연구)")
    ap.add_argument("--start", type=str, default="2021-10-18")
    ap.add_argument("--end", type=str, default="2026-10-16")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--db", type=str, default=str(DB_PATH))
    args = ap.parse_args(argv)

    series = load_series(Path(args.db), args.start, args.end, args.limit)
    if not series:
        logger.error("시계열 없음 — DB/기간 확인")
        return 1
    run_scan(series)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
