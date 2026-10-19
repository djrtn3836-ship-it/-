# -*- coding: utf-8 -*-
"""validation/fundamentals_factor_test.py — 재무(퀄리티) 팩터 횡단면 검증 (2026-10-19).

배경:
    가격(모멘텀·저변동성·롱숏)과 수급(쌍끌이·연속·다이버전스) 팩터는 전부 무엣지
    실증(S128까지). 남은 미개척 중 **이력이 있는 데이터는 재무제표**(DART가 과거
    연도 사업보고서 제공). ROE·부채비율·영업이익률의 횡단면 예측력을 검증한다.

방법:
    - FY2021~2024: 종목별 재무(DART 사업보고서, 연결/별도 자동) → data/fundamentals_history_cache.json
      (--fetch, 재개 가능·실패분 기록)
    - 공시 시점 감안 → **이듬해 4/1 진입**(보수적 가정) → 12개월 보유
    - 연도별 횡단면: 좋은쪽 상위 20% vs 나쁜쪽 하위 20% → 초과수익(유니버스 평균 대비)
    - 판정: 4개 연도 중 3개+ 같은 방향 + 크기 (표본 연 190 안팎 — 연도 일관성 중심)

주의:
    - FY2025는 이듬해 4/1(2026-04)부터 창이 열려 12개월 미완 → 제외(창 미완 표기).
    - 초과수익은 유니버스 평균 대비(시장 방향 제거). 거래비용 미반영.

사용:
    python -m validation.fundamentals_factor_test --fetch        # 재무 이력 수집
    python -m validation.fundamentals_factor_test                # 분석(캐시 사용)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.logger import setup_logger

logger = setup_logger("fundamentals_factor_test")

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "decisions.db"
CACHE_PATH = ROOT / "data" / "fundamentals_history_cache.json"

FY_YEARS: Tuple[str, ...] = ("2021", "2022", "2023", "2024")
# 지표: (필드, 높을수록 좋은가, 표기)
METRICS: Dict[str, Tuple[str, bool, str]] = {
    "ROE": ("roe", True, "높을수록 좋음"),
    "부채비율": ("debt_ratio", False, "낮을수록 좋음"),
    "영업이익률": ("op_margin", True, "높을수록 좋음"),
}
FETCH_DELAY = 0.4


# ── 공용 순수 함수 (테스트 대상) ─────────────────────────────────────

def _to_metric_fields(fin: Dict[str, Any]) -> Dict[str, float]:
    """DART 응답 → {roe, debt_ratio, op_margin} 정규화(결측은 결측대로 유지)."""
    out: Dict[str, float] = {}
    for our, src in (("roe", "ROE"), ("debt_ratio", "부채비율"), ("op_margin", "영업이익률")):
        v = (fin or {}).get(src)
        if v is None:
            continue
        try:
            out[our] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def entry_exit_dates(fy: str) -> Tuple[str, str]:
    """FY 보고서 공시 감안 진입일(이듬해 4/1)·청산 기준일(+12개월)."""
    y = int(fy) + 1
    return f"{y}-04-01", f"{y + 1}-04-01"


def _first_on_or_after(dates: Sequence[str], target: str) -> Optional[int]:
    for i, d in enumerate(dates):
        if d >= target:
            return i
    return None


def period_return(dates: Sequence[str], closes: Sequence[float], fy: str) -> Optional[float]:
    """이듬해 4/1 진입 ~ +12개월 청산 수익률. 날짜 부족 시 None."""
    entry, exit_t = entry_exit_dates(fy)
    i = _first_on_or_after(dates, entry)
    if i is None:
        return None
    j = _first_on_or_after(dates, exit_t)
    if j is None or j <= i:
        return None
    if closes[i] <= 0 or closes[j] <= 0:
        return None
    return closes[j] / closes[i] - 1.0


def has_corporate_action(
    dates: Sequence[str], closes: Sequence[float], start: str, end: str
) -> bool:
    """[start,end] 구간에 일간 ±31% 초과 변동이 있으면 True.

    한국 가격제한은 ±30% — 그를 넘는 종가는 액면분할/감자/무상증자 등
    기업행위 아티팩트(yfinance 비조정 가격)일 가능성이 높다 → 해당 종목-창 제외.
    """
    prev: Optional[float] = None
    for d, c in zip(dates, closes):
        if d < start or d > end:
            prev = None  # 창 경계 리셋 — 창 밖 점프와 비교 금지(테스트가 적발한 버그)
            continue
        if prev and c > 0 and abs(c / prev - 1.0) > 0.31:
            return True
        prev = c if c > 0 else None
    return False


def split_quintile_edges(n: int) -> int:
    """상·하위 그룹 크기(20%, 최소 1)."""
    return max(1, n // 5)


def tstat(vals: Sequence[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    sd = statistics.stdev(vals)
    if sd == 0:
        return 0.0
    return statistics.fmean(vals) / (sd / math.sqrt(n))


def rank_groups(
    values: Dict[str, float], higher_is_better: bool
) -> Tuple[List[str], List[str]]:
    """(좋은쪽 상위 20%, 나쁜쪽 하위 20%) 티커 목록."""
    items = sorted(values.items(), key=lambda kv: kv[1], reverse=higher_is_better)
    k = split_quintile_edges(len(items))
    if len(items) < 2 * k:
        return [], []
    good = [t for t, _ in items[:k]]
    bad = [t for t, _ in items[-k:]]
    return good, bad


# ── 데이터 적재 ─────────────────────────────────────────────────────

def load_price_series(db_path: Path, start: str = "2021-06-01") -> Dict[str, Tuple[List[str], List[float]]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out: Dict[str, Tuple[List[str], List[float]]] = {}
        cur = con.execute(
            "SELECT ticker, date, close FROM ohlcv WHERE ticker NOT LIKE '^%' AND date >= ? "
            "ORDER BY ticker, date", (start,),
        )
        t_cur: Optional[str] = None
        dates: List[str] = []
        closes: List[float] = []
        for t, d, c in cur:
            if t != t_cur:
                if t_cur and len(dates) >= 30:
                    out[t_cur] = (dates, closes)
                t_cur, dates, closes = t, [], []
            if c and float(c) > 0:
                dates.append(str(d))
                closes.append(float(c))
        if t_cur and len(dates) >= 30:
            out[t_cur] = (dates, closes)
        return out
    finally:
        con.close()


def load_cache(path: Path = CACHE_PATH) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_cache(cache: Dict[str, Any], path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    tmp.replace(path)


def fetch_history(
    tickers: Sequence[str],
    *,
    years: Sequence[str] = FY_YEARS,
    cache_path: Path = CACHE_PATH,
    corp_code_fn: Optional[Callable[[str], Optional[str]]] = None,
    fetcher: Optional[Callable[[str, str], Dict[str, float]]] = None,
    delay: float = FETCH_DELAY,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """재무 이력 수집(재개 가능). 캐시 반환."""
    cache = load_cache(cache_path)
    cache.setdefault("tickers", {})
    for ticker in tickers:
        entry = cache["tickers"].setdefault(ticker, {"years": {}})
        corp = entry.get("corp_code")
        if corp is None and corp_code_fn is not None:
            corp = corp_code_fn(ticker)
            entry["corp_code"] = corp
            if corp is None:
                logger.warning(f"[{ticker}] corp_code 없음 — 건너뜀")
                save_cache(cache, cache_path)
                continue
        for y in years:
            if y in entry["years"]:
                continue
            try:
                data = fetcher(corp, y) if fetcher else {}
            except Exception as e:
                data = {}
                logger.warning(f"[{ticker}] FY{y} 조회 실패: {str(e)[:80]}")
            entry["years"][y] = data or None
            save_cache(cache, cache_path)
            sleep(delay)
            logger.info(f"[{ticker}] FY{y}: {'OK' if data else '데이터 없음'}")
    cache["updated_at"] = date.today().isoformat()
    save_cache(cache, cache_path)
    return cache


# ── 분석 ────────────────────────────────────────────────────────────

def analyze(cache: Dict[str, Any], series: Dict[str, Tuple[List[str], List[float]]]) -> None:
    print(f"■ 재무 팩터 검증 — 가격 시계열 {len(series)}종목 / 캐시 {len(cache.get('tickers', {}))}종목")
    results: Dict[str, List[dict]] = {m: [] for m in METRICS}

    for fy in FY_YEARS:
        rows = []  # (ticker, fwd, metric_values)
        excluded = 0
        entry, exit_t = entry_exit_dates(fy)
        for ticker, (dates, closes) in series.items():
            tinfo = cache.get("tickers", {}).get(ticker) or {}
            fin = (tinfo.get("years") or {}).get(fy)
            if not fin:
                continue
            fwd = period_return(dates, closes, fy)
            if fwd is None:
                continue
            if has_corporate_action(dates, closes, entry, exit_t):
                excluded += 1  # 기업행위 아티팩트(yfinance 비조정) — 제외
                continue
            rows.append((ticker, fwd, fin))
        if excluded:
            logger.info(f"FY{fy}: 기업행위 아티팩트 {excluded}종목 제외")
        if len(rows) < 20:
            continue
        fwd_all = [r[1] for r in rows]
        um = statistics.fmean(fwd_all)
        for m, (field, higher, _) in METRICS.items():
            vals = {r[0]: float(r[2][field]) for r in rows if r[2].get(field) is not None}
            if len(vals) < 20:
                continue
            good, bad = rank_groups(vals, higher)
            g_ret = [r[1] for r in rows if r[0] in good]
            b_ret = [r[1] for r in rows if r[0] in bad]
            results[m].append({
                "fy": fy,
                "n": len(vals),
                "good_excess": (statistics.fmean(g_ret) - um) * 100.0,
                "bad_excess": (statistics.fmean(b_ret) - um) * 100.0,
                "spread": (statistics.fmean(g_ret) - statistics.fmean(b_ret)) * 100.0,
            })

    print(f"{'지표':<10}{'FY':>6}{'n':>5}{'상위초과':>9}{'하위초과':>9}{'상-하 스프레드':>12}")
    for m in METRICS:
        for r in results[m]:
            print(f"{m:<10}{r['fy']:>6}{r['n']:>5}{r['good_excess']:>+9.2f}"
                  f"{r['bad_excess']:>+9.2f}{r['spread']:>+12.2f}")
        rs = results[m]
        if rs:
            spreads = [r["spread"] for r in rs]
            good_ex = [r["good_excess"] for r in rs]
            pos_years = sum(1 for x in good_ex if x > 0)
            print(f"  → {m}: 상위초과 평균 {statistics.fmean(good_ex):+.2f}%p "
                  f"(플러스 {pos_years}/{len(rs)}년) · 스프레드 평균 {statistics.fmean(spreads):+.2f}%p "
                  f"· t≈{tstat(spreads):+.2f}")
        print()
    print("판정: 상위초과 플러스 연도 3/4+ 그리고 평균 크기 > 0 → 유효 후보.")
    print("주의: 표본 연 190 내외·4개 연도 — 통계적 검정력 낮음, 연도 일관성 중심 판단.")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="재무(퀄리티) 팩터 검증")
    ap.add_argument("--fetch", action="store_true", help="재무 이력 수집 후 종료")
    ap.add_argument("--years", type=str, default=",".join(FY_YEARS))
    ap.add_argument("--limit", type=int, default=250)
    ap.add_argument("--delay", type=float, default=FETCH_DELAY)
    ap.add_argument("--db", type=str, default=str(DB_PATH))
    args = ap.parse_args(argv)

    if args.fetch:
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except Exception:
            pass
        from infrastructure.dart.client import DartConnector

        con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        try:
            tickers = [r[0] for r in con.execute(
                "SELECT ticker FROM supply_demand GROUP BY ticker "
                "ORDER BY COUNT(*) DESC LIMIT ?", (args.limit,),
            )]
        finally:
            con.close()
        dart = DartConnector(os.getenv("DART_API_KEY", ""))
        if not dart.api_key:
            logger.error("DART_API_KEY 없음 — .env 확인")
            return 1
        years = [y.strip() for y in args.years.split(",") if y.strip()]
        cache = fetch_history(
            tickers, years=years,
            corp_code_fn=dart.get_corp_code_sync,
            fetcher=lambda corp, y: _to_metric_fields(dart.get_financials_sync(corp, y)),
            delay=args.delay,
        )
        ok = sum(1 for t in cache["tickers"].values() for v in t["years"].values() if v)
        print(f"수집 완료: 유효 연도-재무 {ok}건 / 캐시 {CACHE_PATH}")
        return 0

    cache = load_cache()
    if not cache.get("tickers"):
        logger.error("재무 캐시 없음 — 먼저 --fetch 로 수집하세요")
        return 1
    series = load_price_series(Path(args.db))
    analyze(cache, series)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
