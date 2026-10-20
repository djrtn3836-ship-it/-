# -*- coding: utf-8 -*-
"""공시 유형 전수 스크리닝 (S152).

기존 22k 공시 이력을 21개 유형으로 나눠 5일 초과수익(KOSPI 대비)을
엄격 기준으로 재검정한다. 다중검정(Bonferroni) 임계 t≥3.0 적용.

기준: n≥30 · |t|≥3.0 · 평균/중앙값 부호 일치 · 두 구간(≤2023 / ≥2024) 동일 부호
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from validation.disclosure_event_study import (
    HISTORY_PATH,
    KOSPI,
    build_events,
    dedupe_events,
    load_series,
)

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "disclosure_category_screen.json"
MIN_N = 30
T_BONF = 3.0
SPLIT = "2024-01-01"


def _stats(vals: List[float]) -> Dict[str, Any]:
    n = len(vals)
    if n == 0:
        return {"n": 0}
    mean = statistics.mean(vals)
    med = statistics.median(vals)
    sd = statistics.pstdev(vals) if n > 1 else 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    return {
        "n": n,
        "mean": round(mean * 100, 2),
        "median": round(med * 100, 2),
        "t": round(t, 2),
        "pos": round(sum(1 for v in vals if v > 0) / n * 100, 1),
    }


def run_screen(history_path: Path = HISTORY_PATH) -> Dict[str, Any]:
    from data.db_manager import DatabaseManager

    hist = json.loads(history_path.read_text(encoding="utf-8"))
    tickers: Dict[str, Any] = hist.get("tickers", {})
    start = f"{datetime.now().year - hist.get('years', 5) + 1}-01-01"
    end = datetime.now().strftime("%Y-%m-%d")

    async def _load():
        db = DatabaseManager()
        try:
            k_rows = await db.get_ohlcv_range(KOSPI, start, end)
            k_dates, k_closes = load_series(k_rows)
            k_idx = {d[:10]: i for i, d in enumerate(k_dates)}
            events: List[Dict[str, Any]] = []
            for ticker, info in tickers.items():
                rows = await db.get_ohlcv_range(ticker, start, end)
                dates, closes = load_series(rows)
                events += build_events(ticker, info.get("events", []), dates, closes, list(k_idx), k_closes)
            return events
        finally:
            try:
                await db.close()
            except Exception:
                pass

    events = dedupe_events(asyncio.run(_load()))
    by_cat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in events:
        fwd = e.get("fwd") or {}
        if "5" in fwd:
            by_cat[str(e["category"])].append(e)

    rows: List[Dict[str, Any]] = []
    for cat, evs in by_cat.items():
        v5 = [float(e["fwd"]["5"]) for e in evs]
        v1 = [float(e["fwd"]["1"]) for e in evs if "1" in e["fwd"]]
        st5, st1 = _stats(v5), _stats(v1)
        years: Dict[str, List[float]] = defaultdict(list)
        for e in evs:
            years[str(e.get("date", ""))[:4]].append(float(e["fwd"]["5"]))
        year_mean = {y: round(statistics.mean(v) * 100, 2) for y, v in sorted(years.items()) if len(v) >= 3}
        early = [float(e["fwd"]["5"]) for e in evs if str(e.get("date", "")) < SPLIT]
        late = [float(e["fwd"]["5"]) for e in evs if str(e.get("date", "")) >= SPLIT]
        se, sl = _stats(early), _stats(late)

        flags = []
        if st5["n"] < MIN_N:
            flags.append("n부족")
        if abs(st5["t"]) < T_BONF:
            flags.append("t부족")
        if st5["mean"] * st5["median"] <= 0:
            flags.append("부호불일치")
        if se.get("n", 0) >= 15 and sl.get("n", 0) >= 15 and se["mean"] * sl["mean"] <= 0:
            flags.append("구간불일치")
        if year_mean and sum(1 for v in year_mean.values() if v > 0) / len(year_mean) < 0.7:
            flags.append("연도불일치")
        rows.append(
            {
                "category": cat,
                "n": st5["n"],
                "mean5": st5["mean"],
                "median5": st5["median"],
                "t5": st5["t"],
                "pos5": st5["pos"],
                "mean1": st1.get("mean"),
                "t1": st1.get("t"),
                "early": se.get("mean"),
                "late": sl.get("mean"),
                "years": year_mean,
                "flags": flags,
                "pass": not flags,
            }
        )

    rows.sort(key=lambda r: abs(r["t5"]), reverse=True)
    return {
        "range": [start, end],
        "categories_tested": len(rows),
        "t_threshold": T_BONF,
        "min_n": MIN_N,
        "rows": rows,
        "candidates": [r["category"] for r in rows if r["pass"]],
    }


def format_screen(res: Dict[str, Any]) -> str:
    lines = [
        f"공시 유형 전수 스크리닝 ({res['range'][0]} ~ {res['range'][1]})",
        f"기준: n≥{res['min_n']} · |t|≥{res['t_threshold']}(Bonferroni) · 부호/구간/연도 일치",
        "",
        f"{'유형':<12}{'n':>5}{'1일':>8}{'5일':>8}{'t':>7}{'양수%':>7}{'≤23':>7}{'≥24':>7}  판정",
    ]
    for r in res["rows"]:
        lines.append(
            f"{r['category']:<12}{r['n']:>5}{r['mean1'] if r['mean1'] is not None else 0:>7.2f}%"
            f"{r['mean5']:>7.2f}%{r['t5']:>7.2f}{r['pos5']:>6.1f}%"
            f"{r['early'] if r['early'] is not None else 0:>6.2f}%{r['late'] if r['late'] is not None else 0:>6.2f}%"
            f"  {'✅ 통과' if r['pass'] else ', '.join(r['flags'])}"
        )
    cand = res.get("candidates") or []
    lines.append("")
    lines.append("통과 후보: " + (", ".join(cand) if cand else "없음 (기준 미달)"))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-json", action="store_true")
    args = ap.parse_args()
    res = run_screen()
    print(format_screen(res))
    if args.save_json:
        OUT_PATH.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
