# -*- coding: utf-8 -*-
"""공시 이벤트 연구 (S140) — 공시 유형별 초과수익(1/5/20일).

데이터: data/disclosures_history.json(백필) + DB OHLCV(종목·KOSPI).
규칙: 룩어헤드 없음 · ±31% 아티팩트 제외 · 동일 종목/유형 20거래일 중복 제거.
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "disclosures_history.json"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "disclosure_study.json"
KOSPI = "^KS11"
HORIZONS = (1, 5, 20)
ARTIFACT_LIMIT = 0.31
DEDUPE_GAP = 20
MIN_N = 15

CATEGORY_ORDER = [
    "자사주매입", "자사주처분", "유상증자", "무상증자", "감자", "주식분할",
    "최대주주변경", "합병", "회사분할", "영업양수도", "타법인주식취득",
    "신규시설투자", "대표이사변경",
    "수주계약", "실적공시", "불성실공시", "CB/BW", "배당",
    "횡령배임", "소송",
]


def classify_title(title: str) -> Optional[str]:
    """공시 제목 → 연구 대상 유형(없으면 None=기록만)."""
    t = str(title or "").replace(" ", "")
    if "자사주" in t and ("취득" in t or "매입" in t):
        return "자사주매입"
    if "자사주" in t and "처분" in t:
        return "자사주처분"
    if "유상증자" in t:
        return "유상증자"
    if "무상증자" in t:
        return "무상증자"
    if "주식분할" in t or "액면분할" in t:
        return "주식분할"
    if "감자" in t and ("주식" in t or "결정" in t):
        return "감자"
    if "최대주주" in t and ("변경" in t or "매각" in t):
        return "최대주주변경"
    if "공급계약" in t or "수주" in t or "단일판매" in t:
        return "수주계약"
    if "잠정실적" in t or "영업실적" in t:
        return "실적공시"
    if "불성실공시" in t:
        return "불성실공시"
    if "전환사채" in t or "신주인수권부사채" in t:
        return "CB/BW"
    if "배당" in t:
        return "배당"
    if "횡령" in t or "배임" in t:
        return "횡령배임"
    if "소송" in t:
        return "소송"
    if "합병" in t and "종료" not in t:
        return "합병"
    if "회사분할" in t or "인적분할" in t or "물적분할" in t:
        return "회사분할"
    if "영업양수" in t or "영업양도" in t:
        return "영업양수도"
    if "타법인주식" in t:
        return "타법인주식취득"
    if "신규시설투자" in t or "시설투자" in t:
        return "신규시설투자"
    if "대표이사" in t and ("변경" in t or "선임" in t):
        return "대표이사변경"
    return None


def title_flags(title: str) -> Dict[str, bool]:
    """정정/자율공시 플래그 — 신호 품질 분해용."""
    t = str(title or "")
    return {
        "corrected": "[기재정정]" in t or "정정" in t,
        "voluntary": "자율공시" in t,
    }


def parse_date(s: str) -> Optional[datetime]:
    s = str(s or "").strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def load_series(rows: List[Dict[str, Any]]) -> Tuple[List[str], List[float]]:
    """OHLCV rows(오름차순) → (dates, closes). 종가 결측 행 제외."""
    dates: List[str] = []
    closes: List[float] = []
    for r in rows:
        try:
            c = float(r.get("close") or 0.0)
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        dates.append(str(r.get("date")))
        closes.append(c)
    return dates, closes


def has_artifact(closes: List[float], lo: int, hi: int) -> bool:
    """[lo, hi] 구간 일간 변동 ±31% 초과 = 분할/감자 등 아티팩트."""
    lo = max(lo, 1)
    hi = min(hi, len(closes) - 1)
    for i in range(lo, hi + 1):
        prev = closes[i - 1]
        if prev <= 0:
            continue
        if abs(closes[i] / prev - 1.0) > ARTIFACT_LIMIT:
            return True
    return False


def build_events(
    ticker: str,
    events: List[Dict[str, Any]],
    dates: List[str],
    closes: List[float],
    kospi_dates: List[str],
    kospi_closes: List[float],
) -> List[Dict[str, Any]]:
    """공시 원천 → 채점 가능 이벤트(horizon별 초과수익). 룩어헤드 없음."""
    if not dates or len(dates) < 25:
        return []
    k_idx = {d: i for i, d in enumerate(kospi_dates)}
    day_strs = [d[:10] for d in dates]
    out: List[Dict[str, Any]] = []
    for ev in events:
        cat = classify_title(ev.get("title", ""))
        if not cat:
            continue
        dt = parse_date(ev.get("date", ""))
        if not dt:
            continue
        pos = bisect.bisect_left(day_strs, dt.strftime("%Y-%m-%d"))
        if pos >= len(dates):
            continue
        base_date = day_strs[pos]
        k_base = k_idx.get(base_date)
        if k_base is None:
            continue
        row: Dict[str, Any] = {
            "ticker": ticker,
            "category": cat,
            "date": ev.get("date", ""),
            "base": pos,
            "fwd": {},
        }
        any_h = False
        for h in HORIZONS:
            j = pos + h
            if j >= len(dates):
                continue
            fwd_date = day_strs[j]
            k_j = k_idx.get(fwd_date)
            if k_j is None:
                continue
            if has_artifact(closes, pos, j):
                continue
            try:
                stock_ret = closes[j] / closes[pos] - 1.0
                k_ret = kospi_closes[k_j] / kospi_closes[k_base] - 1.0
            except ZeroDivisionError:
                continue
            row["fwd"][str(h)] = stock_ret - k_ret
            any_h = True
        if any_h:
            out.append(row)
    return out


def dedupe_events(events: List[Dict[str, Any]], gap: int = DEDUPE_GAP) -> List[Dict[str, Any]]:
    """동일 (ticker, category) 이벤트가 gap 거래일 내 겹치면 첫 이벤트만 유지."""
    events = sorted(events, key=lambda e: (e["ticker"], e["category"], e["base"]))
    last: Dict[Tuple[str, str], int] = {}
    out: List[Dict[str, Any]] = []
    for e in events:
        key = (e["ticker"], e["category"])
        prev = last.get(key)
        if prev is not None and e["base"] - prev < gap:
            continue
        last[key] = e["base"]
        out.append(e)
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


def summarize(events: List[Dict[str, Any]], years_span: int) -> Dict[str, Any]:
    """유형별·기간별 통계 + 등급."""
    by_cat: Dict[str, Dict[str, Any]] = {}
    for cat in CATEGORY_ORDER:
        rows = [e for e in events if e["category"] == cat]
        if not rows:
            continue
        cat_stats: Dict[str, Any] = {
            "n_events": len(rows),
            "freq_per_year": round(len(rows) / max(years_span, 1), 1),
            "horizons": {},
        }
        for h in HORIZONS:
            vals = [e["fwd"][str(h)] * 100.0 for e in rows if str(h) in e["fwd"]]
            if len(vals) < MIN_N:
                continue
            year_means: Dict[str, List[float]] = defaultdict(list)
            for e in rows:
                if str(h) not in e["fwd"]:
                    continue
                year_means[str(e["date"])[:4]].append(e["fwd"][str(h)] * 100.0)
            pos_years = sum(1 for v in year_means.values() if len(v) >= 5 and statistics.mean(v) > 0)
            sig_years = sum(1 for v in year_means.values() if len(v) >= 5)
            cat_stats["horizons"][str(h)] = {
                "n": len(vals),
                "mean": round(statistics.mean(vals), 3),
                "median": round(statistics.median(vals), 3),
                "hit": round(sum(1 for v in vals if v > 0) / len(vals) * 100.0, 1),
                "t": round(_tstat(vals), 2) if _tstat(vals) is not None else None,
                "pos_years": pos_years,
                "sig_years": sig_years,
                "years": {
                    y: {"n": len(v), "mean": round(statistics.mean(v), 2)}
                    for y, v in sorted(year_means.items())
                },
            }
        by_cat[cat] = cat_stats

    for st in by_cat.values():
        st["grade"] = _grade(st)

    return {"categories": by_cat, "years_span": years_span}


def _grade(st: Dict[str, Any]) -> str:
    best = None
    for h in HORIZONS:
        hs = st["horizons"].get(str(h))
        if not hs:
            continue
        score = abs(hs["t"] or 0.0)
        if best is None or score > abs(best["t"] or 0.0):
            best = hs
    if best is None:
        return "표본부족"
    n = best["n"]
    t = best["t"] or 0.0
    yrs = best["sig_years"]
    pos = best["pos_years"]
    consistent = pos >= max(3, (yrs + 1) // 2) if t > 0 else (yrs - pos) >= max(3, (yrs + 1) // 2)
    # 평균과 중앙값 부호가 다르면 소수 극단값이 평균을 끄는 것 → 신호로 불가
    median_ok = (best["median"] > 0) == (best["mean"] > 0)
    if n >= 30 and abs(t) >= 2.0 and consistent and st["freq_per_year"] >= 3 and median_ok:
        return "A_생존"
    if n >= 30 and abs(t) >= 1.5 and consistent and median_ok:
        return "B_경계"
    if n >= 30 and abs(t) >= 1.0:
        return "C_참고"
    return "표본부족"


def run_study(
    *,
    history_path: Path = HISTORY_PATH,
    kospi: str = KOSPI,
    logger: Any = None,
) -> Dict[str, Any]:
    import asyncio

    from data.db_manager import DatabaseManager

    hist = json.loads(history_path.read_text(encoding="utf-8"))
    tickers: Dict[str, Any] = hist.get("tickers", {})
    start = f"{datetime.now().year - hist.get('years', 5) + 1}-01-01"
    end = datetime.now().strftime("%Y-%m-%d")

    async def _load():
        db = DatabaseManager()
        try:
            k_rows = await db.get_ohlcv_range(kospi, start, end)
            k_dates, k_closes = load_series(k_rows)
            k_idx = {d[:10]: i for i, d in enumerate(k_dates)}
            events: List[Dict[str, Any]] = []
            for ticker, info in tickers.items():
                rows = await db.get_ohlcv_range(ticker, start, end)
                dates, closes = load_series(rows)
                ev = build_events(ticker, info.get("events", []), dates, closes, list(k_idx), k_closes)
                events += ev
            return events, len(k_dates)
        finally:
            try:
                await db.close()
            except Exception:
                pass

    events, k_len = asyncio.run(_load())
    if logger:
        logger.info(f"채점 이벤트 {len(events)}건 (KOSPI {k_len}일)")

    years_span = max(datetime.now().year - (datetime.now().year - hist.get("years", 5) + 1) + 1, 1)
    raw_by_cat = defaultdict(int)
    for t, info in tickers.items():
        for e in info.get("events", []):
            cat = classify_title(e.get("title", ""))
            if cat:
                raw_by_cat[cat] += 1

    deduped = dedupe_events(events)
    result = summarize(deduped, years_span)
    result["raw_counts"] = dict(raw_by_cat)
    result["dedupe_kept"] = len(deduped)
    result["study_range"] = [start, end]
    return result


def format_result(result: Dict[str, Any]) -> str:
    lines = ["공시 이벤트 연구 (초과수익 = 종목 − KOSPI)", ""]
    cats = result.get("categories", {})
    for cat in CATEGORY_ORDER:
        st = cats.get(cat)
        if not st:
            continue
        picks = []
        for h in HORIZONS:
            hs = st["horizons"].get(str(h))
            if not hs:
                continue
            picks.append(
                f"{h}d {hs['mean']:+.2f}% (n={hs['n']} 적중 {hs['hit']:.0f}% t={hs['t']})"
            )
        lines.append(f"[{_grade(st)}] {cat} · 연 {st['freq_per_year']}건 · " + " | ".join(picks))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-json", action="store_true")
    args = ap.parse_args()
    from core.logger import setup_logger

    logger = setup_logger("disclosure_study")
    result = run_study(logger=logger)
    print(format_result(result))
    if args.save_json:
        OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
