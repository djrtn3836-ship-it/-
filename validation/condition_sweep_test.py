# -*- coding: utf-8 -*-
"""validation/condition_sweep_test.py — 상황조건 전수 스캔 (S136① → S137 매크로 확장).

목적:
    "지금 어떤 환경인가"라는 상황 조건이 미래 기대값과 연결되는지 체계적으로
    전수 검증한다. S137: 매크로 시계열(VIX·나스닥·달러·금리·구리) 재료 추가 +
    **신호 빈도·등급 표시**(다중 신호 체계 — 등급 A/B/C).

방법 (기존 검증 관례 그대로):
    - 이벤트 = 조건이 켜지는 첫날만 (연속 중복 제거 — S128 교훈)
    - 성과 = KOSPI 전방수익 − 무조건 평균(기준선), 순열검정 p(무작위 같은 수 날)
    - 등급: A=생존 후보(p≤0.01·n≥10·반기·연도), B=경계(p≤0.05·n≥10·연도)
            C=참고(1) n≥20 & p≤0.20 — **자주 뜨는 신호를 기록·추적하기 위한 등급**
            (빈도 높은 조건도 버리지 않고 모의 기록 대상으로 남긴다)
    - 발동 빈도(건/년)를 함께 표시 — 연 1회 미만 조건은 실사용 불가로 표시

재료: 폭·신고/신저·KOSPI 추세·실현변동성·수급(외국인)·거래량 + **매크로 5종**
사용:
    python -X utf8 -m validation.condition_sweep_test --db data/decisions.db [--save-json]
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from analytics.market_breadth import S, breadth_at, load_index, load_universe
from validation.breadth_timing_test import _tstat, fwd_return, permutation_pvalue

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "decisions.db"
SWEEP_OUT = ROOT / "data" / "condition_sweep.json"

WINDOW_START = "2021-11-01"   # 수급 백필·폭 워밍업 이후 공통 창
HORIZONS = (1, 5, 20, 60)
MIN_EVENTS = 10
TRADING_DAYS_PER_YEAR = 252

# (표시명, ohlcv 특수 티커) — macro_backfill.py가 저장한 것
MACRO_SERIES: List[Tuple[str, str]] = [
    ("VIX", "^VIX"),
    ("나스닥", "^IXIC"),
    ("달러", "DX-Y.NYB"),
    ("미10년", "^TNX"),
    ("구리", "HG=F"),
]


def _flow_market(db_path: Path) -> Tuple[Dict[str, float], Dict[str, float]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        fnet: Dict[str, float] = {}
        inet: Dict[str, float] = {}
        for d, f, i in con.execute(
            "SELECT date, SUM(foreign_net), SUM(institution_net) FROM supply_demand GROUP BY date"
        ):
            fnet[str(d)] = float(f or 0.0)
            inet[str(d)] = float(i or 0.0)
        return fnet, inet
    finally:
        con.close()


def _volume_market(db_path: Path) -> Dict[str, float]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out: Dict[str, float] = {}
        for d, s, c in con.execute(
            "SELECT date, SUM(volume), COUNT(*) FROM ohlcv "
            "WHERE ticker NOT LIKE '^%' AND volume > 0 GROUP BY date"
        ):
            if c:
                out[str(d)] = float(s) / float(c)  # 하루 평균 거래량(구성 변화 완화)
        return out
    finally:
        con.close()


def _daily_scan(universe: Dict[str, S], dates: Sequence[str]) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float]]:
    """일별 폭·신고수·신저수(20일 창) 일괄 계산 — 날짜당 1회 스캔."""
    breadth: Dict[str, float] = {}
    nh: Dict[str, float] = {}
    nl: Dict[str, float] = {}
    for d in dates:
        p = breadth_at(universe, d)
        if p is not None:
            breadth[d] = round(p, 2)
        n = nh_cnt = nl_cnt = 0
        for s in universe.values():
            i = bisect.bisect_right(s.dates, d) - 1
            if i < 0 or s.dates[i] != d or i < 20:
                continue
            n += 1
            if s.high[i] >= max(s.high[i - 20: i]):
                nh_cnt += 1
            if s.low[i] <= min(s.low[i - 20: i]):
                nl_cnt += 1
        if n >= 20:
            nh[d] = nh_cnt / n * 100.0
            nl[d] = nl_cnt / n * 100.0
    return breadth, nh, nl


def _streak_days(series: Dict[str, float], dates: Sequence[str], sign: int, length: int = 3) -> List[str]:
    """연속 sign(±) 부호 일수 ≥ length가 처음 도달한 날(중복 제거)."""
    out: List[str] = []
    run = 0
    prev_hit = False
    for d in sorted(dates):
        v = series.get(d)
        if v is None:
            run = 0
            prev_hit = False
            continue
        if (v > 0 and sign > 0) or (v < 0 and sign < 0):
            run += 1
        else:
            run = 0
        hit = run >= length
        if hit and not prev_hit:
            out.append(d)
        prev_hit = hit
    return out


def _first_days(vals: Dict[str, float], cond_fn: Callable[[float], bool]) -> List[str]:
    out: List[str] = []
    prev = False
    for d in sorted(vals):
        now = cond_fn(vals[d])
        if now and not prev:
            out.append(d)
        prev = now
    return out


def _pct_rank_threshold(vals: Sequence[float], q: float) -> float:
    if not vals:
        return 0.0
    xs = sorted(vals)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def asof_align(series: Optional[S], target_dates: Sequence[str]) -> Dict[str, float]:
    """시계열을 기준일 목록에 정렬 — 각 기준일의 '마지막 알려진 값'(미국 휴장일 대응).

    예: KST 종가 시점에 알 수 있는 미국 전일 종가를 쓴다(룩어헤드 없음).
    """
    if not series or not series.dates:
        return {}
    out: Dict[str, float] = {}
    j = -1
    for d in target_dates:
        while j + 1 < len(series.dates) and series.dates[j + 1] <= d:
            j += 1
        if j >= 0:
            out[d] = series.close[j]
    return out


def pct_change(aligned: Dict[str, float], k: int) -> Dict[str, float]:
    """정렬된 시계열의 k스텝 퍼센트 변화(%)."""
    keys = sorted(aligned)
    out: Dict[str, float] = {}
    for i in range(k, len(keys)):
        prev = aligned[keys[i - k]]
        if prev:
            out[keys[i]] = (aligned[keys[i]] / prev - 1.0) * 100.0
    return out


def abs_change(aligned: Dict[str, float], k: int) -> Dict[str, float]:
    """정렬된 시계열의 k스텝 절대 변화(금리처럼 %p 단위)."""
    keys = sorted(aligned)
    out: Dict[str, float] = {}
    for i in range(k, len(keys)):
        out[keys[i]] = aligned[keys[i]] - aligned[keys[i - k]]
    return out


def build_conditions(db_path: Path) -> Tuple[Dict[str, List[str]], Dict[str, Any], S]:
    """시리즈 구축 + 조건 이벤트 맵. 반환: (조건→날짜, 시리즈 dict, 트림된 KOSPI)."""
    index_full = load_index(db_path)
    universe = load_universe(db_path)
    kospi_dates = [d for d in index_full.dates if d >= WINDOW_START]
    breadth, nh, nl = _daily_scan(universe, kospi_dates)
    vol = _volume_market(db_path)
    fnet, inet = _flow_market(db_path)

    kospi = S(
        [d for d in index_full.dates if d >= WINDOW_START],
        [c for d, c in zip(index_full.dates, index_full.close) if d >= WINDOW_START],
        [], [],
    )

    def _ret(k: int) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for i in range(k, len(kospi.dates)):
            out[kospi.dates[i]] = (kospi.close[i] / kospi.close[i - k] - 1.0) * 100.0
        return out

    k5 = _ret(5)
    r1 = _ret(1)
    kvol: Dict[str, float] = {}
    rets = [r1.get(d, 0.0) for d in kospi.dates]
    for i in range(20, len(kospi.dates)):
        w = rets[i - 19: i + 1]
        if len(w) >= 20:
            kvol[kospi.dates[i]] = statistics.stdev(w) * math.sqrt(252.0)
    vol_ratio: Dict[str, float] = {}
    vdates = sorted(d for d in vol if d >= WINDOW_START)
    for i, d in enumerate(vdates):
        if i < 20:
            continue
        base = statistics.fmean([vol[x] for x in vdates[i - 20: i]])
        if base > 0:
            vol_ratio[d] = vol[d] / base

    breadth_chg: Dict[str, float] = {}
    bdates = sorted(breadth)
    for i in range(5, len(bdates)):
        breadth_chg[bdates[i]] = breadth[bdates[i]] - breadth[bdates[i - 5]]

    kv = list(kvol.values())
    hi_thr = _pct_rank_threshold(kv, 0.90)
    lo_thr = _pct_rank_threshold(kv, 0.10)

    # ── 매크로 정렬(S137) ──────────────────────────────
    macro: Dict[str, Dict[str, float]] = {}
    for name, tk in MACRO_SERIES:
        macro[name] = asof_align(load_index(db_path, tk), kospi.dates)
    vix = macro.get("VIX", {})
    nasdaq5 = pct_change(macro.get("나스닥", {}), 5)
    usd20 = pct_change(macro.get("달러", {}), 20)
    rate20 = abs_change(macro.get("미10년", {}), 20)
    copper20 = pct_change(macro.get("구리", {}), 20)
    gap5: Dict[str, float] = {
        d: k5[d] - nasdaq5[d] for d in k5 if d in nasdaq5
    }

    series: Dict[str, Any] = {
        "breadth": breadth, "nh": nh, "nl": nl, "kospi5d": k5,
        "kvol": kvol, "vol_ratio": vol_ratio, "fnet": fnet, "inet": inet,
        "breadth_chg": breadth_chg, "vix": vix, "nasdaq5": nasdaq5,
        "usd20": usd20, "rate20": rate20, "copper20": copper20, "gap5": gap5,
    }

    cond: Dict[str, List[str]] = {}
    cond["폭≤10"] = _first_days(breadth, lambda v: v <= 10)
    cond["폭≤15"] = _first_days(breadth, lambda v: v <= 15)
    cond["폭≤20"] = _first_days(breadth, lambda v: v <= 20)
    cond["폭≥80"] = _first_days(breadth, lambda v: v >= 80)
    cond["폭5일급락(-25p)"] = _first_days(breadth_chg, lambda v: v <= -25)
    cond["신저비율≥30%"] = _first_days(nl, lambda v: v >= 30)
    cond["신저≥신고×3"] = _first_days(
        {d: (nl.get(d, 0) + 1.0) / (nh.get(d, 0) + 1.0) for d in nl}, lambda v: v >= 3.0
    )
    cond["KOSPI5일≤-5%"] = _first_days(k5, lambda v: v <= -5.0)
    cond["KOSPI5일≥+5%"] = _first_days(k5, lambda v: v >= 5.0)
    cond["변동성상위10%"] = _first_days(kvol, lambda v: v >= hi_thr)
    cond["변동성하위10%"] = _first_days(kvol, lambda v: v <= lo_thr)
    cond["외인3일순매도"] = _streak_days(fnet, kospi.dates, -1, 3)
    cond["외인3일순매수"] = _streak_days(fnet, kospi.dates, +1, 3)
    cond["거래량≥MA20×1.5"] = _first_days(vol_ratio, lambda v: v >= 1.5)
    cond["폭≤15&하락중"] = [
        d for d in cond["폭≤15"] if k5.get(d) is not None and k5[d] < 0
    ]
    cond["폭≤15&반등중"] = [
        d for d in cond["폭≤15"] if k5.get(d) is not None and k5[d] >= 0
    ]
    # ── 매크로 조건(S137 신규 10종) ─────────────────────
    cond["VIX≥30"] = _first_days(vix, lambda v: v >= 30.0)
    cond["VIX≤15"] = _first_days(vix, lambda v: v <= 15.0)
    cond["VIX5일+50%"] = _first_days(pct_change(vix, 5), lambda v: v >= 50.0)
    cond["나스닥5일≤-5%"] = _first_days(nasdaq5, lambda v: v <= -5.0)
    cond["나스닥5일≥+5%"] = _first_days(nasdaq5, lambda v: v >= 5.0)
    cond["달러20일+3%"] = _first_days(usd20, lambda v: v >= 3.0)
    cond["미금리20일+30bp"] = _first_days(rate20, lambda v: v >= 0.30)
    cond["미금리20일-30bp"] = _first_days(rate20, lambda v: v <= -0.30)
    cond["구리20일≤-10%"] = _first_days(copper20, lambda v: v <= -10.0)
    cond["KOSPI-나스닥괴리≤-5p"] = _first_days(gap5, lambda v: v <= -5.0)
    return cond, series, kospi


def evaluate_condition(kospi: S, dates: Sequence[str], h: int) -> Optional[Dict[str, Any]]:
    pos = {d: i for i, d in enumerate(kospi.dates)}
    base_all = [
        r for i in range(len(kospi.close))
        if (r := fwd_return(kospi.close, i, h)) is not None
    ]
    if not base_all:
        return None
    base_mean = statistics.fmean(base_all)
    rets: List[float] = []
    yearly: Dict[str, List[float]] = defaultdict(list)
    for d in dates:
        i = pos.get(d)
        if i is None:
            continue
        r = fwd_return(kospi.close, i, h)
        if r is None:
            continue
        ex = r - base_mean
        rets.append(ex)
        yearly[d[:4]].append(ex)
    if not rets:
        return None
    half = len(rets) // 2
    first_half = statistics.fmean(rets[:half]) if half else 0.0
    second_half = statistics.fmean(rets[half:]) if len(rets) - half else 0.0
    years_pos = sum(1 for v in yearly.values() if statistics.fmean(v) > 0)
    span_years = max(1e-9, len(kospi.dates) / TRADING_DAYS_PER_YEAR)
    return {
        "n": len(rets),
        "freq": len(rets) / span_years,
        "excess": statistics.fmean(rets) * 100.0,
        "hit": sum(1 for x in rets if x > 0) / len(rets) * 100.0,
        "t": _tstat(rets),
        "half1": first_half * 100.0,
        "half2": second_half * 100.0,
        "years_pos": f"{years_pos}/{len(yearly)}",
        "years_ok": years_pos >= 0.6 * len(yearly),
    }


def verdict(st: Dict[str, Any], p: float) -> str:
    """등급 판정 — A(생존)/B(경계)/C(참고: 자주 뜨는 신호를 기록·추적)."""
    if p <= 0.01 and st["n"] >= MIN_EVENTS and st["half1"] >= 0 and st["half2"] >= 0 and st["years_ok"]:
        return "A_생존"
    if p <= 0.05 and st["n"] >= MIN_EVENTS and st["years_ok"]:
        return "B_경계"
    if st["n"] >= 20 and p <= 0.20:
        return "C_참고"
    return "-"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="상황조건 전수 스캔 (매크로 확장판)")
    ap.add_argument("--db", type=str, default=str(DB_PATH))
    ap.add_argument("--save-json", action="store_true")
    args = ap.parse_args(argv)

    db = Path(args.db)
    print("시리즈 구축 중 (폭·신고/신저·수급·거래량·매크로)...")
    cond, series, kospi = build_conditions(db)
    if not kospi.dates:
        print("KOSPI 데이터 없음")
        return 1
    have_macro = [k for k, v in series.items() if k in ("vix", "nasdaq5") and v]
    if not have_macro:
        print("※ 매크로 시리즈 없음 — `python -m scheduler.macro_backfill` 먼저 실행 권장")

    results: Dict[str, Any] = {}
    print(f"■ 상황조건 전수 스캔 — 창 {WINDOW_START}~, 조건 {len(cond)}종 · 빈도·등급 포함\n")
    for h in HORIZONS:
        print(f"── 전방 {h}일 ──")
        for name, dates in cond.items():
            st = evaluate_condition(kospi, dates, h)
            if not st:
                print(f"  {name:<18} 이벤트 없음")
                continue
            pv = permutation_pvalue(kospi, dates, h, n_iter=1000)
            v = verdict(st, pv["p"]) if h == 5 else ""
            results.setdefault(name, {})[f"h{h}"] = {**st, "perm_p": pv["p"], "verdict": v}
            flag = f"  ← [{v}]" if v and v != "-" else ""
            print(
                f"  {name:<18} n={st['n']:>3} 빈도 {st['freq']:>4.1f}/년 초과 {st['excess']:+.2f}%p "
                f"적중 {st['hit']:.0f}% t={st['t']:+.2f} p={pv['p']:.3f} 반기 {st['half1']:+.1f}/{st['half2']:+.1f} "
                f"연도 {st['years_pos']}{flag}"
            )
        print()

    grades: Dict[str, List[str]] = {"A_생존": [], "B_경계": [], "C_참고": []}
    for n, r in results.items():
        v = r.get("h5", {}).get("verdict")
        if v in grades:
            grades[v].append(n)
    print("등급 (전방 5일 기준):")
    print(f"  A_생존: {grades['A_생존'] or '없음'}")
    print(f"  B_경계: {grades['B_경계'] or '없음'}")
    print(f"  C_참고(빈도 높은 약신호 — 기록·추적 대상): {grades['C_참고'] or '없음'}")
    print("※ A/B만 표시·알림 후보. C는 모의 기록으로 축적 후 승격 판정(사용자 의도: 신호 다양화).")

    if args.save_json:
        SWEEP_OUT.write_text(
            json.dumps({"window_start": WINDOW_START, "results": results},
                       ensure_ascii=True, indent=1),
            encoding="utf-8",
        )
        print(f"저장: {SWEEP_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
