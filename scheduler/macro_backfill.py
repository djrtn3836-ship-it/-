# -*- coding: utf-8 -*-
"""scheduler/macro_backfill.py — 매크로 시계열 5년 백필 (S137).

배경:
    조건 스캔(S136)의 재료가 폭·수급·추세뿐이었다. 국면 재료(공포지수·미국증시·
    달러·금리·구리)를 `ohlcv` 특수 티커로 DB화하면 **같은 검증 도구로 즉시**
    조건을 확장할 수 있다(index_backfill 패턴 재사용 — 스키마/리더 수정 불필요).

시리즈(전부 무료·무인증 yfinance):
    ^VIX(공포지수) · ^IXIC(나스닥) · DX-Y.NYB(달러인덱스) · ^TNX(미10년금리) · HG=F(구리)

특징:
    - MIN_ROWS 미만이면 저장 생략 + 경고(과거 ^KS200 1행 사고 재발 방지)
    - fetcher 주입 가능(오프라인 테스트) · 멱등 upsert(save_ohlcv_batch)

사용:
    python -m scheduler.macro_backfill            # 5y
    python -m scheduler.macro_backfill --period 2y
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.logger import setup_logger
from scheduler.index_backfill import _fetch_index

logger = setup_logger("macro_backfill")

# (이름, yfinance 후보 목록 — 앞에서부터 시도)
MACRO_SERIES: List[Tuple[str, List[str]]] = [
    ("VIX", ["^VIX"]),
    ("나스닥", ["^IXIC"]),
    ("달러인덱스", ["DX-Y.NYB"]),
    ("미10년", ["^TNX"]),
    ("구리", ["HG=F"]),
]
MIN_ROWS = 250  # 1년 미만 시리즈는 저장하지 않는다

Fetcher = Callable[[str, str], List[Dict[str, Any]]]


async def backfill_macro(
    db: Any,
    *,
    period: str = "5y",
    fetcher: Optional[Fetcher] = None,
    min_rows: int = MIN_ROWS,
) -> Dict[str, int]:
    """매크로 시계열을 ohlcv에 저장. {이름: 저장 행수} 반환(실패/부족은 0)."""
    fetch = fetcher or _fetch_index
    out: Dict[str, int] = {}
    for name, candidates in MACRO_SERIES:
        saved = 0
        for yf_ticker in candidates:
            try:
                rows = fetch(yf_ticker, period)
            except Exception as e:
                logger.warning(f"{name}({yf_ticker}) 조회 실패: {e}")
                rows = []
            if len(rows) < min_rows:
                logger.warning(f"{name}({yf_ticker}) {len(rows)}행 — 다음 후보/생략")
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
    ap = argparse.ArgumentParser(description="매크로 시계열 백필 (yfinance)")
    ap.add_argument("--period", type=str, default="5y", help="기간(기본 5y)")
    args = ap.parse_args(argv)

    from data.db_manager import DatabaseManager

    db = DatabaseManager()
    try:
        res = await backfill_macro(db, period=args.period)
    finally:
        await db.close()
    logger.info(f"매크로 백필 결과: {res}")
    return 0 if any(res.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
