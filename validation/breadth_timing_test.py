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
    python -m validation.breadth_timing_test --robust   # 강건성(임계 스윕·순열검정·KOSDAQ)
"""

from __future__ import annotations

import argparse
import bisect
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from core.logger import setup_logger

# 계산 함수는 프로덕션(analytics/market_breadth.py)과 공용 — S133 이관.
# (이 모듈에서 재수출 유지: 기존 테스트/호출부 호환)
from analytics.market_breadth import (  # noqa: F401
    S,
    _idx_le,
    breadth_at,
    load_index,
    load_universe,
    save_history,
)

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


def threshold_events(breadths: Dict[str, float], thr: float) -> List[str]:
    """폭 <= thr 조건의 '켜지는 첫날' 목록(임계값 스윕 공용)."""
    out: List[str] = []
    prev = False
    for d in sorted(breadths):
        now = breadths[d] <= thr
        if now and not prev:
            out.append(d)
        prev = now
    return out


def _excess_means(index: S, h: int) -> tuple:
    """(일별 초과수익 dict position->excess, 기준선 평균) — 순열검정/스윕 공용."""
    rets = {}
    for i in range(len(index.close)):
        r = fwd_return(index.close, i, h)
        if r is not None:
            rets[i] = r
    base = statistics.fmean(rets.values()) if rets else 0.0
    return {i: r - base for i, r in rets.items()}, base


def permutation_pvalue(
    index: S,
    event_dates: Sequence[str],
    h: int,
    n_iter: int = 2000,
    seed: int = 42,
) -> Dict[str, float]:
    """이벤트 시점 평균 초과수익이 '무작위 같은 수의 날' 대비 얼마나 예외적인가.

    단순 순열(복원 없이 무작위 날짜 n개 추출) — 군집성/자기상관을 보수적으로 반영.
    p = (obs 이상 횟수 + 1) / (n_iter + 1), 단측.
    """
    excess, _ = _excess_means(index, h)
    if not excess:
        return {"obs": 0.0, "p": 1.0, "n": 0}
    pos = {d: i for i, d in enumerate(index.dates)}
    ev_idx = [pos[d] for d in event_dates if d in pos and pos[d] in excess]
    if not ev_idx:
        return {"obs": 0.0, "p": 1.0, "n": 0}
    obs = statistics.fmean([excess[i] for i in ev_idx])
    rng = random.Random(seed)
    pool = list(excess)
    k = len(ev_idx)
    cnt = 0
    for _ in range(n_iter):
        m = statistics.fmean([excess[i] for i in rng.sample(pool, k)])
        if m >= obs:
            cnt += 1
    return {"obs": obs * 100.0, "p": (cnt + 1) / (n_iter + 1), "n": k}


def threshold_sweep(
    index: S,
    breadths: Dict[str, float],
    h: int,
    thresholds: Sequence[float] = (5, 8, 10, 12, 15, 20, 25),
) -> Dict[float, dict]:
    """임계값별 (이벤트 수·초과·적중·t) — 10%만 튀면 우연, 넓게 완만하면 실재."""
    excess, _ = _excess_means(index, h)
    out: Dict[float, dict] = {}
    for thr in thresholds:
        vals = []
        for d in threshold_events(breadths, thr):
            i = _idx_le(index.dates, d)
            if i >= 0 and index.dates[i] == d and i in excess:
                vals.append(excess[i])
        if not vals:
            out[thr] = {"n": 0}
            continue
        out[thr] = {
            "n": len(vals),
            "excess": statistics.fmean(vals) * 100.0,
            "hit": sum(1 for x in vals if x > 0) / len(vals) * 100.0,
            "t": _tstat(vals),
        }
    return out


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
    ap.add_argument("--robust", action="store_true", help="강건성: 임계 스윕·순열검정·KOSDAQ·구간 제거")
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
        n = save_history(breadths, HISTORY_OUT)
        logger.info(f"폭 역사 저장: {HISTORY_OUT} ({n}일)")

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

    if args.robust:
        print("\n■ [강건성 ①] 임계값 스윕 (KOSPI 전방 5일, 첫날 중복제거)")
        kq = load_index(db, "^KQ11")
        for thr, st in threshold_sweep(index, breadths, 5).items():
            if st.get("n", 0) == 0:
                continue
            pv = permutation_pvalue(index, threshold_events(breadths, thr), 5)
            print(f"  ≤{thr:>2.0f}%  n={st['n']:>3} 초과 {st['excess']:+.2f}%p "
                  f"적중 {st['hit']:.0f}% t={st['t']:+.2f}  순열p={pv['p']:.3f}")
        print("\n■ [강건성 ②] L2(≤10%) 상세")
        l2 = events["L2_폭10이하"]
        pv = permutation_pvalue(index, l2, 5)
        print(f"  KOSPI 5일: 관측 {pv['obs']:+.2f}%p · 무작위 2,000회 순열p={pv['p']:.3f} (n={pv['n']})")
        if kq.dates:
            st_kq = evaluate(kq, {"L2_폭10이하": l2}, 5)["L2_폭10이하"]
            st_kq20 = evaluate(kq, {"L2_폭10이하": l2}, 20)["L2_폭10이하"]
            print(f"  KOSDAQ 5일 : n={st_kq['n']}, 초과 {st_kq['excess']:+.2f}%p, "
                  f"적중 {st_kq['hit']:.0f}%, t={st_kq['t']:+.2f}")
            print(f"  KOSDAQ 20일: n={st_kq20['n']}, 초과 {st_kq20['excess']:+.2f}%p, "
                  f"적중 {st_kq20['hit']:.0f}%, t={st_kq20['t']:+.2f}")
        no_26 = [d for d in l2 if not d.startswith("2026")]
        st_x = evaluate(index, {"L2_폭10이하": no_26}, 5)["L2_폭10이하"]
        print(f"  2026년 제외: n={st_x['n']}, 초과 {st_x['excess']:+.2f}%p, t={st_x['t']:+.2f}")

    print("\n판정: |t|>2 + 연도 일관성(플러스 연도 3/4+)이면 레짐 신호 후보.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
