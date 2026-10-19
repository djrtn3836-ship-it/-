# -*- coding: utf-8 -*-
"""무상증자 진입/청산 정밀 검증 (S142).

질문: 16:40 알림(종가 후) → 다음날 진입해도 엣지가 남는가?
- E0: 공시일 종가 진입(기존 연구) / E1: 다음 거래일 종가 / E2: 다음 거래일 시가
- 청산: 고정 5일 vs 손절 -3% vs 목표 +7% vs 손절+목표
- 조건 분해: 시장 폭(≤15), KOSPI 5일 추세, 거래량 배수
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from validation.disclosure_event_study import classify_title, parse_date

HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "disclosures_history.json"
BREADTH_PATH = Path(__file__).resolve().parent.parent / "data" / "breadth_history.json"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "disclosure_entry_study.json"
KOSPI = "^KS11"
HORIZONS = (1, 3, 5, 10, 20)
ARTIFACT_LIMIT = 0.31
TARGET_CAT = "무상증자"


def load_series(rows: List[Dict[str, Any]]) -> Tuple[List[str], List[float], List[float], List[float]]:
    dates, opens, closes, vols = [], [], [], []
    for r in rows:
        try:
            c = float(r.get("close") or 0.0)
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        dates.append(str(r.get("date"))[:10])
        closes.append(c)
        try:
            o = float(r.get("open") or 0.0)
        except (TypeError, ValueError):
            o = 0.0
        opens.append(o)
        try:
            vols.append(float(r.get("volume") or 0.0))
        except (TypeError, ValueError):
            vols.append(0.0)
    return dates, opens, closes, vols


def has_artifact(closes: List[float], lo: int, hi: int) -> bool:
    lo = max(lo, 1)
    hi = min(hi, len(closes) - 1)
    for i in range(lo, hi + 1):
        prev = closes[i - 1]
        if prev > 0 and abs(closes[i] / prev - 1.0) > ARTIFACT_LIMIT:
            return True
    return False


def breadth_at(history: Dict[str, float], day: str) -> Optional[float]:
    if not history:
        return None
    key = str(day)[:10]
    if key in history:
        return history[key]
    prior = [d for d in history if d <= key]
    if not prior:
        return None
    return history[max(prior)]


def build_rows(
    ticker: str,
    events: List[Dict[str, Any]],
    dates: List[str],
    opens: List[float],
    closes: List[float],
    vols: List[float],
    k_dates: List[str],
    k_closes: List[float],
    breadth: Dict[str, float],
    *,
    cat: str = TARGET_CAT,
) -> List[Dict[str, Any]]:
    """무상증자 이벤트 → 진입/청산/조건 분해용 행."""
    if len(dates) < 30:
        return []
    k_idx = {d: i for i, d in enumerate(k_dates)}
    out: List[Dict[str, Any]] = []
    for ev in events:
        if classify_title(ev.get("title", "")) != cat:
            continue
        dt = parse_date(ev.get("date", ""))
        if not dt:
            continue
        base = bisect.bisect_left(dates, dt.strftime("%Y-%m-%d"))
        if base + 2 >= len(dates):
            continue
        if has_artifact(closes, base - 1, base + 21):
            continue
        if dates[base] not in k_idx:
            continue

        def kret(d0: str, d1: str) -> Optional[float]:
            i0, i1 = k_idx.get(d0), k_idx.get(d1)
            if i0 is None or i1 is None:
                return None
            return k_closes[i1] / k_closes[i0] - 1.0

        row: Dict[str, Any] = {
            "ticker": ticker,
            "date": ev.get("date", ""),
            "base": base,
            "breadth": breadth_at(breadth, dates[base]),
            "fwd": {},
            "exits": {},
        }
        prev5 = kret(dates[base - 5], dates[base]) if base >= 5 else None
        row["kospi5_pre"] = prev5
        base_vol = vols[base]
        avg20 = statistics.mean(v for v in vols[max(0, base - 19):base + 1] if v > 0) if any(
            v > 0 for v in vols[max(0, base - 19):base + 1]
        ) else 0.0
        row["vol_ratio"] = base_vol / avg20 if avg20 > 0 else None

        entries = {
            "E0": (base, closes[base]),
            "E1": (base + 1, closes[base + 1]),
            "E2": (base + 1, opens[base + 1] if opens[base + 1] > 0 else closes[base + 1]),
        }
        for name, (eidx, price) in entries.items():
            for h in HORIZONS:
                j = eidx + h
                if j >= len(dates) or price <= 0:
                    continue
                kr = kret(dates[eidx], dates[j])
                if kr is None:
                    continue
                row["fwd"][f"{name}_{h}"] = (closes[j] / price - 1.0) - kr

        eidx, price = entries["E1"]
        for h in (5,):
            j = eidx + h
            if j >= len(dates) or price <= 0:
                continue
            kr = kret(dates[eidx], dates[j])
            if kr is None:
                continue
            row["exits"]["hold5"] = (closes[j] / price - 1.0) - kr
        for rule, thr, sign in (("stop3", -0.03, -1), ("target7", 0.07, 1)):
            for d in range(1, 6):
                j = eidx + d
                if j >= len(dates):
                    break
                raw = closes[j] / price - 1.0
                if (sign < 0 and raw <= thr) or (sign > 0 and raw >= thr):
                    kr = kret(dates[eidx], dates[j])
                    if kr is not None:
                        raw -= kr
                    row["exits"][rule] = raw
                    break
        out.append(row)
    return out


def dedupe_rows(rows: List[Dict[str, Any]], gap: int = 20) -> List[Dict[str, Any]]:
    """동일 종목 이벤트가 gap 거래일 내 겹치면 첫 이벤트만 유지(overlap t 부풀림 방지)."""
    rows = sorted(rows, key=lambda r: (r["ticker"], r["base"]))
    last: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []
    for r in rows:
        prev = last.get(r["ticker"])
        if prev is not None and r["base"] - prev < gap:
            continue
        last[r["ticker"]] = r["base"]
        out.append(r)
    return out


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
        "mean": round(statistics.mean(vals), 2),
        "median": round(statistics.median(vals), 2),
        "hit": round(sum(1 for v in vals if v > 0) / len(vals) * 100.0, 1),
        "t": round(t, 2) if t is not None else None,
    }


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    entry_keys = [f"{e}_{h}" for e in ("E0", "E1", "E2") for h in HORIZONS]
    table = {k: stats([r["fwd"][k] * 100.0 for r in rows if k in r["fwd"]]) for k in entry_keys}
    exits = {
        k: stats([(r.get("exits") or {}).get(k) * 100.0 for r in rows
                  if k in (r.get("exits") or {})])
        for k in ("hold5", "stop3", "target7")
    }
    rules: Dict[str, Any] = {}
    for name in ("stop3", "target7"):
        vals = []
        for r in rows:
            ex = r.get("exits") or {}
            trig = ex.get(name)
            hold = ex.get("hold5")
            val = trig if trig is not None else hold
            if val is not None:
                vals.append(val * 100.0)
        rules[name] = {"stats": stats(vals), "triggered": sum(
            1 for r in rows if name in (r.get("exits") or {})
        )}

    def split(name: str, pred) -> Dict[str, Any]:
        sel = [r for r in rows if pred(r)]
        return {"n": len(sel), "stats": stats([r["fwd"]["E1_5"] * 100.0 for r in sel if "E1_5" in r["fwd"]])}

    conds = {
        "breadth_le15": split("b", lambda r: r.get("breadth") is not None and r["breadth"] <= 15),
        "breadth_gt15": split("b", lambda r: r.get("breadth") is None or r["breadth"] > 15),
        "kospi5_down": split("k", lambda r: r.get("kospi5_pre") is not None and r["kospi5_pre"] < 0),
        "kospi5_up": split("k", lambda r: r.get("kospi5_pre") is None or r["kospi5_pre"] >= 0),
        "vol_x2": split("v", lambda r: r.get("vol_ratio") is not None and r["vol_ratio"] >= 2.0),
        "vol_lt2": split("v", lambda r: r.get("vol_ratio") is None or r["vol_ratio"] < 2.0),
    }
    return {"n": len(rows), "entries": table, "exits": exits, "rules": rules, "conditions": conds}


def format_result(res: Dict[str, Any]) -> str:
    lines = [f"무상증자 진입/청산 (n={res['n']})", ""]
    lines.append("== 진입 시점별 초과수익(5일) ==")
    for e, label in (("E0", "공시일 종가"), ("E1", "다음날 종가"), ("E2", "다음날 시가")):
        s = res["entries"].get(f"{e}_5") or {}
        if s:
            lines.append(f"  {e} {label}: {s['mean']:+.2f}% (중앙 {s['median']:+.2f}%, 적중 {s['hit']}%, t={s['t']}, n={s['n']})")
    lines.append("== 청산 규칙 (E1 진입) ==")
    s = res["exits"].get("hold5") or {}
    if s:
        lines.append(f"  고정 5일: {s['mean']:+.2f}% (중앙 {s['median']:+.2f}%, 적중 {s['hit']}%, n={s['n']})")
    for k, label in (("stop3", "손절 -3% 규칙"), ("target7", "목표 +7% 규칙")):
        r = (res.get("rules") or {}).get(k) or {}
        st = r.get("stats") or {}
        if st:
            lines.append(f"  {label}: {st['mean']:+.2f}% (중앙 {st['median']:+.2f}%, 적중 {st['hit']}%, n={st['n']},"
                         f" 발동 {r.get('triggered')}건)")
    lines.append("== 조건 분해 (E1, 5일) ==")
    for k, label in (("breadth_le15", "폭≤15"), ("breadth_gt15", "폭>15"),
                     ("kospi5_down", "KOSPI 5일 하락"), ("kospi5_up", "KOSPI 5일 상승"),
                     ("vol_x2", "거래량 2배↑"), ("vol_lt2", "거래량 2배 미만")):
        c = res["conditions"].get(k) or {}
        s = c.get("stats") or {}
        if s:
            lines.append(f"  {label}: n={s['n']} {s['mean']:+.2f}% (중앙 {s['median']:+.2f}%, 적중 {s['hit']}%)")
    return "\n".join(lines)


def run_study(*, history_path: Path = HISTORY_PATH, cat: str = TARGET_CAT,
              logger: Any = None) -> Dict[str, Any]:
    import asyncio

    from analytics.market_breadth import load_breadth_history
    from data.db_manager import DatabaseManager

    hist = json.loads(history_path.read_text(encoding="utf-8"))
    tickers = hist.get("tickers", {})
    start = "2021-10-19"
    end = datetime.now().strftime("%Y-%m-%d")
    breadth = load_breadth_history(BREADTH_PATH)

    async def _load():
        db = DatabaseManager()
        try:
            k_rows = await db.get_ohlcv_range(KOSPI, start, end)
            k_dates, _, k_closes, _ = load_series(k_rows)
            rows: List[Dict[str, Any]] = []
            for ticker, info in tickers.items():
                events = [e for e in info.get("events", [])
                          if classify_ok(e.get("title", ""), cat)]
                if not events:
                    continue
                r = await db.get_ohlcv_range(ticker, start, end)
                dates, opens, closes, vols = load_series(r)
                rows += build_rows(ticker, events, dates, opens, closes, vols, k_dates, k_closes,
                                   breadth, cat=cat)
            return rows
        finally:
            try:
                await db.close()
            except Exception:
                pass

    rows = dedupe_rows(asyncio.run(_load()))
    if logger:
        logger.info(f"{cat} 이벤트 {len(rows)}건(중복 제거)")
    return summarize(rows)


def classify_ok(title: str, cat: str = TARGET_CAT) -> bool:
    return classify_title(title) == cat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-json", action="store_true")
    ap.add_argument("--cat", default=TARGET_CAT)
    args = ap.parse_args()
    from core.logger import setup_logger

    logger = setup_logger("disclosure_entry")
    res = run_study(cat=args.cat, logger=logger)
    print(format_result(res))
    if args.save_json:
        merged: Dict[str, Any] = {}
        if OUT_PATH.exists():
            try:
                merged = json.loads(OUT_PATH.read_text(encoding="utf-8"))
            except Exception:
                merged = {}
        merged[args.cat] = res
        OUT_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
