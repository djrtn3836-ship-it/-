# -*- coding: utf-8 -*-
"""validation/signal_vs_random_test.py — 알림 신호 vs 무작위 진입 정식 비교 (S136, ⑤).

목적:
    우리 시스템이 실제로 기록한 알림 신호(paper_signals.jsonl)가
    같은 날 무작위 진입보다 나은지 **최종 성적표**로 판정한다.

방법:
    - 신호: 기록된 (종목, 날짜). 비교 기준가 = 두 집단 모두 **신호일 종가**
      (신호 가격은 장중 기록이라 기준 불일치 — 종가 기준으로 통일).
    - 초과 = 신호 종목 전방수익 − 같은 날 유니버스 평균 전방수익 (교차면).
    - 순열: 날짜별로 같은 수의 종목을 무작위 추출한 평균 초과의 분포와 비교
      (n_iter회, 시드 고정) → 단측 p.
    - 표본 부족(n<30)이면 "잠정" 경고 — 매주 실행하면 판정 가능해진다.

사용:
    python -X utf8 -m validation.signal_vs_random_test
    python -X utf8 -m validation.signal_vs_random_test --signals logs/paper_signals.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from analytics.market_breadth import S, load_universe

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "decisions.db"
SIGNALS_PATH = ROOT / "logs" / "paper_signals.jsonl"
HORIZONS = (1, 5, 20)
MIN_N = 30
ARTIFACT = 0.40  # 전방 구간 일변동 ±40% 초과(분할류) 제외


def load_signals(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("ticker") and r.get("date"):
                rows.append(r)
    except FileNotFoundError:
        return []
    # 같은 (종목, 날짜) 중복은 1건만
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
        key = (str(r["ticker"]), str(r["date"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def fwd(series: Dict[str, S], idx_map: Dict[str, Dict[str, int]], ticker: str, date: str, h: int) -> Optional[float]:
    s = series.get(ticker)
    if not s:
        return None
    i = idx_map.get(ticker, {}).get(date)
    if i is None or i + h >= len(s.close):
        return None
    if s.close[i] <= 0 or s.close[i + h] <= 0:
        return None
    if any(abs(s.close[j] / s.close[j - 1] - 1.0) > ARTIFACT for j in range(i + 1, min(len(s.close), i + h + 1))):
        return None
    return s.close[i + h] / s.close[i] - 1.0


def cross_section_for_date(series: Dict[str, S], idx_map: Dict[str, Dict[str, int]], date: str, h: int):
    """그 날짜의 (티커 → 전방수익) 맵(전 종목)."""
    out: Dict[str, float] = {}
    for t, _s in series.items():
        if date not in idx_map.get(t, {}):
            continue
        r = fwd(series, idx_map, t, date, h)
        if r is not None:
            out[t] = r
    return out


def run_horizon(
    signals: Sequence[Dict[str, Any]],
    series: Dict[str, S],
    idx_map: Dict[str, Dict[str, int]],
    h: int,
    n_iter: int = 1000,
    seed: int = 42,
) -> Dict[str, Any]:
    by_date: Dict[str, List[str]] = defaultdict(list)
    for r in signals:
        by_date[str(r["date"])].append(str(r["ticker"]))

    css: Dict[str, Dict[str, float]] = {}
    excesses: List[float] = []
    for d, tickers in sorted(by_date.items()):
        cs = css.setdefault(d, cross_section_for_date(series, idx_map, d, h))
        if len(cs) < 30:
            continue
        base = statistics.fmean(cs.values())
        for t in tickers:
            if t in cs:
                excesses.append(cs[t] - base)
    if not excesses:
        return {"n": 0}

    obs = statistics.fmean(excesses)
    rng = random.Random(seed)
    cnt = 0
    days = [(d, ts) for d, ts in sorted(by_date.items()) if len(css.get(d, {})) >= 30]
    for _ in range(n_iter):
        total: List[float] = []
        for d, tickers in days:
            cs = css[d]
            pool = list(cs)
            k = min(len(tickers), len(pool))
            base = statistics.fmean(cs.values())
            for t in rng.sample(pool, k):
                total.append(cs[t] - base)
        if total and statistics.fmean(total) >= obs:
            cnt += 1
    p = (cnt + 1) / (n_iter + 1)
    sd = statistics.stdev(excesses) if len(excesses) > 1 else 0.0
    t = obs / (sd / math.sqrt(len(excesses))) if sd else 0.0
    return {
        "n": len(excesses),
        "excess": obs * 100.0,
        "hit": sum(1 for x in excesses if x > 0) / len(excesses) * 100.0,
        "t": t,
        "p": p,
        "by_day": {d: len(v) for d, v in sorted(by_date.items())},
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="알림 신호 vs 무작위 진입 비교")
    ap.add_argument("--db", type=str, default=str(DB_PATH))
    ap.add_argument("--signals", type=str, default=str(SIGNALS_PATH))
    args = ap.parse_args(argv)

    signals = load_signals(Path(args.signals))
    if not signals:
        print("신호 기록 없음(paper_signals.jsonl)")
        return 1
    series = load_universe(Path(args.db), min_days=30)
    idx_map = {t: {d: i for i, d in enumerate(s.dates)} for t, s in series.items()}

    dates = sorted({str(r["date"]) for r in signals})
    print(f"■ 알림 신호 vs 무작위 — 신호 {len(signals)}건 ({dates[0]}~{dates[-1]})")
    print("  기준가 = 신호일 종가(양쪽 통일) · 초과 = 같은 날 유니버스 평균 차감 · 순열 1,000회\n")
    for h in HORIZONS:
        st = run_horizon(signals, series, idx_map, h)
        if not st.get("n"):
            print(f"  전방 {h}일: 유효 표본 없음(아직 미성숙)")
            continue
        warn = " ⚠ 잠정(표본 부족 — n<30)" if st["n"] < MIN_N else ""
        print(
            f"  전방 {h:>2}일: n={st['n']:>3} 초과 {st['excess']:+.2f}%p 적중 {st['hit']:.0f}% "
            f"t={st['t']:+.2f} 순열p={st['p']:.3f}{warn}"
        )
    print("\n판정: 순열 p≤0.05 + n≥30 + (가능하면) 일자 일관성. 표본이 차면 매주 재실행.")
    print("※ 신호 자체가 무작위와 구분되지 않으면 그 사실을 그대로 인정한다(정직성 원칙).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
