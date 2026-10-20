# -*- coding: utf-8 -*-
"""자사주매입 본문 규모 연구 (S143) — 취득비율·거래대금 배수 vs 초과수익.

본문(document.xml)에서 취득예정주식·금액·소각계획을 파싱해
'규모가 클수록 신호가 강한가'를 검정한다. 룩어헤드 없음.
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "disclosures_history.json"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "buyback_body_study.json"
KOSPI = "^KS11"
CAT = "자사주매입"
HORIZONS = (1, 5, 20)


def _tstat(vals: List[float]) -> Optional[float]:
    n = len(vals)
    if n < 2:
        return None
    m = statistics.mean(vals)
    sd = statistics.pstdev(vals)
    if sd == 0:
        return None
    return m / (sd / (n ** 0.5))


def stats(vals: List[float]) -> Dict[str, Any]:
    if not vals:
        return {}
    t = _tstat(vals)
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 3),
        "median": round(statistics.median(vals), 3),
        "hit": round(sum(1 for v in vals if v > 0) / len(vals) * 100.0, 1),
        "t": round(t, 2) if t is not None else None,
    }


def spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) < 5 or len(xs) != len(ys):
        return None

    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        for pos, idx in enumerate(order):
            r[idx] = float(pos)
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=False))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 3) if den else None


def build_rows(
    ticker: str,
    events: List[Dict[str, Any]],
    dates: List[str],
    closes: List[float],
    vols: List[float],
    k_dates: List[str],
    k_closes: List[float],
    *,
    body_fn: Any,
) -> List[Dict[str, Any]]:
    """자사주매입 이벤트 → 본문 규모 + 전방 초과수익."""
    from validation.disclosure_entry_test import has_artifact
    from validation.disclosure_event_study import classify_title, parse_date

    if len(dates) < 30:
        return []
    k_idx = {d: i for i, d in enumerate(k_dates)}
    out: List[Dict[str, Any]] = []
    for ev in events:
        if classify_title(ev.get("title", "")) != CAT:
            continue
        rcept = str(ev.get("rcept_no") or "")
        dt = parse_date(ev.get("date", ""))
        if not rcept or not dt:
            continue
        base = bisect.bisect_left(dates, dt.strftime("%Y-%m-%d"))
        if base + 1 >= len(dates) or has_artifact(closes, base - 1, base + 20):
            continue
        body = body_fn(rcept)
        if not body or not body.get("ok"):
            continue
        k_base = k_idx.get(dates[base])
        if k_base is None:
            continue
        row: Dict[str, Any] = {
            "ticker": ticker,
            "date": ev.get("date", ""),
            "base": base,
            "ratio_pct": body.get("ratio_pct"),
            "buy_amount": body.get("buy_amount"),
            "cancel_flag": bool(body.get("cancel_flag")),
            "fwd": {},
        }
        avg_turnover = statistics.mean(
            [closes[i] * vols[i] for i in range(max(0, base - 19), base + 1) if vols[i] > 0]
        ) if any(vols[i] > 0 for i in range(max(0, base - 19), base + 1)) else 0.0
        amt = body.get("buy_amount")
        row["amt_days"] = round(amt / avg_turnover, 2) if amt and avg_turnover > 0 else None
        for h in HORIZONS:
            j = base + h
            if j >= len(dates):
                continue
            k_j = k_idx.get(dates[j])
            if k_j is None:
                continue
            stock = closes[j] / closes[base] - 1.0
            kret = k_closes[k_j] / k_closes[k_base] - 1.0
            row["fwd"][str(h)] = round(stock - kret, 6)
        if row["fwd"]:
            out.append(row)
    return out


def dedupe_rows(rows: List[Dict[str, Any]], gap: int = 20) -> List[Dict[str, Any]]:
    rows = sorted(rows, key=lambda r: (r["ticker"], int(r.get("base", 0))))
    last: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    for r in rows:
        idx = int(r.get("base", 0))
        prev = last.get(r["ticker"])
        if prev is not None and idx - prev < gap:
            continue
        last[r["ticker"]] = idx
        out.append(r)
    return out


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    def vals(sel, h="1"):
        return [r["fwd"][h] * 100.0 for r in sel if h in r["fwd"]]

    with_amt = [r for r in rows if r.get("amt_days") is not None]
    big = [r for r in with_amt if r["amt_days"] >= 1.0]
    small = [r for r in with_amt if r["amt_days"] < 1.0]
    with_ratio = [r for r in rows if r.get("ratio_pct") is not None]
    ratio_big = [r for r in with_ratio if r["ratio_pct"] >= 1.0]
    ratio_small = [r for r in with_ratio if r["ratio_pct"] < 1.0]
    cancel = [r for r in rows if r.get("cancel_flag")]
    no_cancel = [r for r in rows if not r.get("cancel_flag")]

    pairs1 = [(r["amt_days"], r["fwd"]["1"]) for r in with_amt if "1" in r["fwd"]]
    pairs5 = [(r["amt_days"], r["fwd"]["5"]) for r in with_amt if "5" in r["fwd"]]
    corr1 = spearman([p[0] for p in pairs1], [p[1] for p in pairs1])
    corr5 = spearman([p[0] for p in pairs5], [p[1] for p in pairs5])

    return {
        "n": len(rows),
        "with_amt": len(with_amt),
        "amt": {"all": stats(vals(with_amt)), "big": stats(vals(big)), "small": stats(vals(small)),
                "big5": stats(vals(big, "5")), "small5": stats(vals(small, "5"))},
        "ratio": {"all": stats(vals(with_ratio)), "big": stats(vals(ratio_big)),
                  "small": stats(vals(ratio_small)),
                  "big5": stats(vals(ratio_big, "5")), "small5": stats(vals(ratio_small, "5"))},
        "cancel": {"yes": stats(vals(cancel)), "no": stats(vals(no_cancel))},
        "corr_amt_days_1d": corr1,
        "corr_amt_days_5d": corr5,
    }


def format_result(res: Dict[str, Any]) -> str:
    def line(label, s):
        if not s:
            return f"  {label}: -"
        return f"  {label}: {s['mean']:+.2f}% (중앙 {s['median']:+.2f}%, 적중 {s['hit']}%, t={s['t']}, n={s['n']})"

    lines = [f"자사주매입 본문 규모 (n={res['n']}, 본문확보 {res['with_amt']})", ""]
    lines.append("== 거래대금 배수(취득금액/20일 평균거래대금) ==")
    lines.append(line("전체 1d", res["amt"]["all"]))
    lines.append(line("≥1배 1d", res["amt"]["big"]))
    lines.append(line("<1배 1d", res["amt"]["small"]))
    lines.append(line("≥1배 5d", res["amt"]["big5"]))
    lines.append(line("<1배 5d", res["amt"]["small5"]))
    lines.append("== 취득비율(발행주식 대비) ==")
    lines.append(line("≥1% 1d", res["ratio"]["big"]))
    lines.append(line("<1% 1d", res["ratio"]["small"]))
    lines.append(line("≥1% 5d", res["ratio"]["big5"]))
    lines.append(line("<1% 5d", res["ratio"]["small5"]))
    lines.append("== 소각 계획 ==")
    lines.append(line("소각 언급", res["cancel"]["yes"]))
    lines.append(line("소각 없음", res["cancel"]["no"]))
    lines.append(f"상관(amt_days vs 1d): {res['corr_amt_days_1d']} · (vs 5d): {res['corr_amt_days_5d']}")
    return "\n".join(lines)


def run_study(*, history_path: Path = HISTORY_PATH, limit: Optional[int] = None,
              logger: Any = None) -> Dict[str, Any]:
    import asyncio

    from data.db_manager import DatabaseManager
    from scheduler.disclosure_body import CACHE_PATH, get_buyback

    hist = json.loads(history_path.read_text(encoding="utf-8"))
    tickers = hist.get("tickers", {})
    start = "2021-10-19"
    end = datetime.now().strftime("%Y-%m-%d")

    async def _load():
        db = DatabaseManager()
        try:
            k_rows = await db.get_ohlcv_range(KOSPI, start, end)
            from validation.disclosure_entry_test import load_series

            k_dates, _, k_closes, _ = load_series(k_rows)
            rows: List[Dict[str, Any]] = []
            for ticker, info in tickers.items():
                if limit and len(rows) >= limit:
                    break
                r = await db.get_ohlcv_range(ticker, start, end)
                dates, _, closes, vols = load_series(r)
                rows += build_rows(ticker, info.get("events", []), dates, closes, vols,
                                   k_dates, k_closes, body_fn=lambda rn: get_buyback(rn))
            return rows
        finally:
            try:
                await db.close()
            except Exception:
                pass

    rows = asyncio.run(_load())
    # 거래일 인덱스 기반 중복 제거를 위해 base 재계산 없이 (ticker,date) 정렬 후 근사 제거
    rows = dedupe_rows(rows)
    if logger:
        logger.info(f"자사주매입 본문 행 {len(rows)}건 (캐시 {CACHE_PATH})")
    return summarize(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-json", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    from core.logger import setup_logger

    logger = setup_logger("buyback_body")
    res = run_study(limit=args.limit, logger=logger)
    print(format_result(res))
    if args.save_json:
        OUT_PATH.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
