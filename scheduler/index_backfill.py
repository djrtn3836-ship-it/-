# -*- coding: utf-8 -*-
"""scheduler/index_backfill.py — 지수 시계열(KOSPI/KOSDAQ) DB화.

배경 (2026-10-16 냉철 평가):
    전략/모멘텀 성과의 벤치마크가 '유니버스 동일가중'(생존편향 포함)뿐이었다.
    지수 시계열을 `ohlcv` 테이블에 저장(티커 "^KS11" 등)하면 정확한 시장 기준선으로
    비교할 수 있다(기존 리더기 수정 불필요 — ohlcv 스키마 재사용).

특징:
    - yfinance 공개 API(무인증) — 본 앱/백필과 충돌 없음
    - 시리즈가 min_rows 미만이면 **저장 생략 + 경고**(과거 ^KS200 1행 사고 재발 방지)
    - fetcher 주입 가능(오프라인 테스트)

사용:
    python -m scheduler.index_backfill            # 5y
    python -m scheduler.index_backfill --period 1y
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logger import setup_logger

logger = setup_logger("index_backfill")

# 지수별 후보(앞에서부터 시도 — 실패/행 부족 시 다음 후보).
# 🔴 2026-10-19: ^KS200은 yfinance에서 1행만 반환(S106 실측) →
#   KODEX 200 ETF(069500.KS) 프록시로 대체(가격 스케일 무관 — 수익률 추적).
INDEX_CANDIDATES: List[Tuple[str, List[str]]] = [
    ("KOSPI", ["^KS11"]),
    ("KOSDAQ", ["^KQ11"]),
    ("KOSPI200", ["^KS200", "069500.KS"]),
]
MIN_ROWS = 60  # 3개월 미만 시리즈는 저장하지 않는다(무음 오염 방지)

Fetcher = Callable[[str, str], List[Dict[str, Any]]]


def _fetch_index(yf_ticker: str, period: str) -> List[Dict[str, Any]]:
    """yfinance로 지수 일봉 조회 → 표준 dict 목록(날짜 오름차순)."""
    import yfinance as yf

    df = yf.download(yf_ticker, period=period, interval="1d", progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        return []
    try:
        if getattr(df.columns, "nlevels", 1) > 1:
            df = df.copy()
            df.columns = df.columns.get_level_values(0)
    except Exception:
        pass
    rows: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        try:
            close = float(row.get("Close"))
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue

        def _f(key: str, default: float, row: Any = row) -> float:
            try:
                v = float(row.get(key))
                return v if v > 0 else default
            except (TypeError, ValueError):
                return default

        try:
            vol = int(row.get("Volume") or 0)
        except (TypeError, ValueError):
            vol = 0
        rows.append({
            "date": idx.strftime("%Y-%m-%d"),
            "open": _f("Open", close),
            "high": _f("High", close),
            "low": _f("Low", close),
            "close": close,
            "volume": max(0, vol),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


async def backfill_indices(
    db: Any,
    *,
    period: str = "5y",
    fetcher: Optional[Fetcher] = None,
    min_rows: int = MIN_ROWS,
) -> Dict[str, int]:
    """지수 시계열을 ohlcv에 저장. {지수명: 저장 행수} 반환(실패/부족은 0)."""
    fetch = fetcher or _fetch_index
    out: Dict[str, int] = {}
    for name, candidates in INDEX_CANDIDATES:
        saved = 0
        for yf_ticker in candidates:
            try:
                rows = fetch(yf_ticker, period)
            except Exception as e:
                logger.warning(f"{name}({yf_ticker}) 조회 실패: {e}")
                rows = []
            if len(rows) < min_rows:
                logger.warning(
                    f"{name}({yf_ticker}) {len(rows)}행 — 다음 후보/생략"
                )
                continue
            recs: List[Tuple[Any, ...]] = [
                (
                    yf_ticker,
                    r["date"],
                    r.get("open", 0.0),
                    r.get("high", 0.0),
                    r.get("low", 0.0),
                    r.get("close", 0.0),
                    r.get("volume", 0),
                )
                for r in rows
            ]
            try:
                n = await db.save_ohlcv_batch(recs)
                saved = int(n or 0)
                logger.info(
                    f"{name}: {saved}행 저장 ({rows[0]['date']} ~ {rows[-1]['date']}, "
                    f"source={yf_ticker})"
                )
                break
            except Exception as e:
                logger.warning(f"{name}({yf_ticker}) 저장 실패: {e}")
        out[name] = saved
    return out


async def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="지수 시계열 백필 (yfinance)")
    ap.add_argument("--period", type=str, default="5y", help="기간(기본 5y)")
    args = ap.parse_args(argv)

    from data.db_manager import DatabaseManager

    db = DatabaseManager()
    try:
        res = await backfill_indices(db, period=args.period)
    finally:
        await db.close()
    logger.info(f"지수 백필 결과: {res}")
    return 0 if any(res.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
