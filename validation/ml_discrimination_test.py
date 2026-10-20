# -*- coding: utf-8 -*-
"""validation/ml_discrimination_test.py — 자체 ML/신뢰도 예측의 판별력 검증 (S135).

배경:
    S115부터 진입 신호 중 신뢰도 < 0.60은 '저신뢰'로 억제(알림 필터)한다.
    이 필터와 ML(ml_score)·종합 점수(score)가 실제로 결과를 구분하는지는
    검증된 적이 없다. S134에서 outcome 정합성(캐치업·5d 고정) 확보 후 검증 재개.
    ※ 실측: 기록된 BUY의 신뢰도는 0.610~0.705 — 0.60 필터는 실제로 발동 안 함
      (분석기 자체가 ≥0.61만 BUY로 기록). 즉 실질 필터 = 분석기 경계값.

방법:
    decisions ⋈ decision_outcomes(확정분) → 5분위.
    성과 = **초과수익**: 개별 − 같은 날·같은 행동 평균 (일자 교란 제거 —
    flow_factor_test 관례). 아티팩트 필터: |1d|>70% · |5d|>300% 제외(분할류).
    ml_score=0.5(ML 미적용 기본값)는 분위에서 분리해 별도 보고.
    판정: |t|>2 + 분위 단조 + 일자 일관성. 단일 t 신뢰 금지(S128 교훈).

주의: 현재 확정 = 2026-10-12~16 (4거래일, 급락 주간) → 표본 한계 명시.

사용:
    python -X utf8 -m validation.ml_discrimination_test
    python -X utf8 -m validation.ml_discrimination_test --db data/decisions.db
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "decisions.db"

ENTRY_ACTIONS = ("BUY", "SIGNAL_ENTRY")
ARTIFACT_1D = 70.0    # |1일 초과| — 한국 가격제한(±30%) 감안해도 아티팩트 수준
ARTIFACT_5D = 300.0
N_BINS = 5
ML_DEFAULT = 0.5      # ML 미적용 시 저장되는 기본값


def load_rows(db_path: Path) -> List[Dict[str, Any]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.execute(
            """SELECT d.id, d.ticker, d.action, d.confidence, d.ml_score, d.score,
                      d.created_at, o.return_1d, o.return_5d, o.is_correct
               FROM decisions d JOIN decision_outcomes o ON o.decision_id = d.id
               WHERE o.is_correct IS NOT NULL"""
        )
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
        for r in rows:
            r["date"] = str(r.get("created_at") or "")[:10]
        return rows
    finally:
        con.close()


def filter_rows(
    rows: Sequence[Dict[str, Any]],
    actions: Optional[Sequence[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """행동 필터 + 아티팩트 제외. 반환: (유지 행, 제외 카운트)."""
    dropped = {"artifact_1d": 0, "artifact_5d": 0, "no_key": 0}
    out: List[Dict[str, Any]] = []
    for r in rows:
        if actions is not None and str(r.get("action")) not in actions:
            continue
        r1 = r.get("return_1d")
        r5 = r.get("return_5d")
        if r1 is None or float(r1) != float(r1):
            dropped["no_key"] += 1
            continue
        if abs(float(r1)) > ARTIFACT_1D:
            dropped["artifact_1d"] += 1
            continue
        if r5 is not None and abs(float(r5)) > ARTIFACT_5D:
            dropped["artifact_5d"] += 1
            continue
        out.append(r)
    return out, dropped


def add_excess(rows: List[Dict[str, Any]]) -> None:
    """같은 날·같은 행동 평균을 차감해 일자 교란을 제거(제자리 수정)."""
    groups: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    groups5: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for r in rows:
        g = (r["date"], str(r["action"]))
        groups[g].append(float(r["return_1d"]))
        if r.get("return_5d") is not None:
            groups5[g].append(float(r["return_5d"]))
    for r in rows:
        g = (r["date"], str(r["action"]))
        base = statistics.fmean(groups[g])
        r["ex1d"] = float(r["return_1d"]) - base
        if r.get("return_5d") is not None and groups5.get(g):
            r["ex5d"] = float(r["return_5d"]) - statistics.fmean(groups5[g])
        else:
            r["ex5d"] = None


def quantile_bins(
    rows: Sequence[Dict[str, Any]], key: str, n_bins: int = N_BINS
) -> List[Tuple[int, List[Dict[str, Any]], float, float]]:
    """키 기준 n분위. 반환: [(1..n, 행들, 최소값, 최대값)] — 동률은 순위로 분배."""
    vals = [(float(r[key]), i) for i, r in enumerate(rows) if r.get(key) is not None]
    if not vals:
        return []
    vals.sort()
    vals = vals[: len(vals) // n_bins * n_bins] if len(vals) >= n_bins else vals
    size = max(1, len(vals) // n_bins)
    out = []
    for b in range(n_bins):
        chunk = vals[b * size: (b + 1) * size]
        if not chunk:
            continue
        bin_rows = [rows[i] for _, i in chunk]
        out.append((b + 1, bin_rows, chunk[0][0], chunk[-1][0]))
    return out


def _tstat(vals: Sequence[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    sd = statistics.stdev(vals)
    return statistics.fmean(vals) / (sd / math.sqrt(n)) if sd else 0.0


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    ex1 = [float(r["ex1d"]) for r in rows]
    ex5 = [float(r["ex5d"]) for r in rows if r.get("ex5d") is not None]
    hits = [1 for r in rows if r.get("is_correct")]
    return {
        "n": len(rows),
        "ex1d": statistics.fmean(ex1) if ex1 else 0.0,
        "t": _tstat(ex1),
        "ex5d": statistics.fmean(ex5) if ex5 else None,
        "n5": len(ex5),
        "hit": sum(hits) / len(rows) * 100.0 if rows else 0.0,
    }


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """순위 상관(동률은 평균 순위). 표본 부족 시 0."""
    n = len(xs)
    if n < 3:
        return 0.0

    def rank(v: Sequence[float]) -> List[float]:
        order = sorted(range(n), key=lambda i: v[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk

    rx, ry = rank(xs), rank(ys)
    try:
        return float(statistics.correlation(rx, ry))
    except Exception:
        return 0.0


def welch_t(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    denom = math.sqrt(statistics.variance(a) / len(a) + statistics.variance(b) / len(b))
    if denom == 0:
        return 0.0
    return (statistics.fmean(a) - statistics.fmean(b)) / denom


def _bin_section(title: str, rows: List[Dict[str, Any]], key: str) -> float:
    """분위 섹션 출력. 반환: 상위-하위 Welch t."""
    print(f"\n■ {title}")
    bins = quantile_bins(rows, key)
    if len(bins) < 2:
        print("  표본 부족 — 생략")
        return 0.0
    top: List[Dict[str, Any]] = []
    bottom: List[Dict[str, Any]] = []
    for b, bin_rows, lo, hi in bins:
        st = summarize(bin_rows)
        ex5 = f"{st['ex5d']:+.2f}" if st["ex5d"] is not None else "  n/a"
        print(
            f"  Q{b} n={st['n']:>5} {key} {lo:.3f}~{hi:.3f} | "
            f"초과1d {st['ex1d']:+.3f} t={st['t']:+.2f} | 초과5d {ex5}(n{st['n5']}) | 적중 {st['hit']:.0f}%"
        )
        if b == 1:
            bottom = bin_rows
        if b == len(bins):
            top = bin_rows
    pairs = [(float(r[key]), float(r["ex1d"])) for r in rows if r.get(key) is not None]
    rho = spearman([p[0] for p in pairs], [p[1] for p in pairs])
    wt = welch_t([float(r["ex1d"]) for r in top], [float(r["ex1d"]) for r in bottom])
    print(f"  Spearman({key}, 초과1d) = {rho:+.3f} | 상위Q-Q1 Welch t = {wt:+.2f}")
    return wt


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="자체 ML/신뢰도 판별력 검증")
    ap.add_argument("--db", type=str, default=str(DB_PATH))
    args = ap.parse_args(argv)

    rows = load_rows(Path(args.db))
    if not rows:
        print("확정 outcome 없음 — 17:00 학습 실행 후 재시도")
        return 1

    entries, drop_e = filter_rows(rows, ENTRY_ACTIONS)
    add_excess(entries)
    holds, drop_h = filter_rows(rows, ("HOLD",))
    add_excess(holds)

    dates = sorted({r["date"] for r in entries})
    print(f"■ 자체 ML/신뢰도 판별력 검증 — 확정 {len(rows)}건 · 진입 {len(entries)}건 · 기간 {dates[0]}~{dates[-1]} ({len(dates)}일)")
    print(f"  (아티팩트 제외: |1d|>{ARTIFACT_1D:.0f}% {drop_e['artifact_1d']}건 · |5d|>{ARTIFACT_5D:.0f}% {drop_e['artifact_5d']}건)")
    print("  성과 = 초과수익(개별 − 같은 날·같은 행동 평균, %)")

    ml_default = [r for r in entries if r.get("ml_score") is not None and float(r["ml_score"]) == ML_DEFAULT]
    entries_ml = [r for r in entries if r.get("ml_score") is not None and float(r["ml_score"]) != ML_DEFAULT]
    if ml_default:
        st = summarize(ml_default)
        print(f"\n  ※ ml_score={ML_DEFAULT}(ML 미적용) {st['n']}건: 초과1d {st['ex1d']:+.3f} 적중 {st['hit']:.0f}% — 분위에서 분리")

    t_conf = _bin_section("진입 신호 — confidence 5분위 (0.60 필터 실체 검증)", entries, "confidence")
    t_ml = _bin_section("진입 신호 — ml_score 5분위 (ML 적용분만)", entries_ml, "ml_score")
    t_score = _bin_section("진입 신호 — 종합 score 5분위", entries, "score")

    print("\n■ 일자별 (confidence 상위Q-Q1 초과1d, %p)")
    bins = quantile_bins(entries, "confidence")
    if len(bins) >= 2:
        top_ids = {id(r) for r in bins[-1][1]}
        bot_ids = {id(r) for r in bins[0][1]}
        for d in dates:
            td = [float(r["ex1d"]) for r in entries if r["date"] == d and id(r) in top_ids]
            bd = [float(r["ex1d"]) for r in entries if r["date"] == d and id(r) in bot_ids]
            diff = (statistics.fmean(td) - statistics.fmean(bd)) if td and bd else 0.0
            print(f"  {d}: {diff:+.3f} (n {len(td)}/{len(bd)})")

    if holds:
        print(f"\n■ HOLD(조용함 예측) — ml_score 5분위 · 적중=|1d|<2% (n={len(holds)})")
        holds_ml = [r for r in holds if r.get("ml_score") is not None and float(r["ml_score"]) != ML_DEFAULT]
        hbins = quantile_bins(holds_ml, "ml_score")
        if len(hbins) < 2:
            print("  표본 부족 — 생략")
        for b, bin_rows, lo, hi in hbins:
            st = summarize(bin_rows)
            print(f"  Q{b} n={st['n']:>5} ml {lo:.3f}~{hi:.3f} | 조용함 적중 {st['hit']:.0f}%")

    print("\n판정 기준: |t|>2 + 분위 단조 + 일자 일관성. 표본이 짧으면(4일) 참고치 — 단정 금지.")
    print(f"요약: confidence t={t_conf:+.2f} · ml_score t={t_ml:+.2f} · score t={t_score:+.2f} (상위-하위)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
