# -*- coding: utf-8 -*-
"""scheduler/ohlcv_backfill.py - 과거 일봉(OHLCV) 백필 유틸.

배경:
    운영 DB의 `ohlcv` 테이블이 **0건**이라 백테스트(`validation/backtest_runner`)와
    성과 지표가 모두 0이었다.
    키움 `request_tr(..., "일봉")`은 단일 시점 조회(dt=어제)라 과거 이력 확보에
    부적합 → 프로젝트에서 이미 사용 중인 `yfinance`(거시 수집기)로 일봉 이력을 백필한다.

특징:
    - 네트워크 접근은 `fetch_history()`에 격리 → 테스트에서 fetcher 주입 가능(오프라인)
    - `.KS`(KOSPI) 실패 시 `.KQ`(KOSDAQ) 자동 시도
    - 저장은 `db.save_ohlcv_batch()`(배치, UNIQUE(ticker,date) → 중복 안전)

사용:
    python -m scheduler.ohlcv_backfill --period 1y --limit 20
    python -m scheduler.ohlcv_backfill --tickers 005930,000660 --period 2y
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.logger import setup_logger

logger = setup_logger("ohlcv_backfill")

MARKET_SUFFIXES: Tuple[str, ...] = (".KS", ".KQ")  # KOSPI, KOSDAQ
Fetcher = Callable[[str, str], List[Dict[str, Any]]]


def _flatten_columns(df: Any) -> Any:
    """yfinance의 MultiIndex 컬럼(Price, Ticker)을 단일 레벨로 평탄화."""
    try:
        if getattr(df.columns, "nlevels", 1) > 1:
            df = df.copy()
            df.columns = df.columns.get_level_values(0)
    except Exception:
        pass
    return df


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        if out != out:  # NaN
            return default
        return out
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        out = int(value)
        return out if out >= 0 else default
    except Exception:
        return default


def default_fetcher(yf_ticker: str, period: str) -> List[Dict[str, Any]]:
    """yfinance로 일봉 이력을 조회해 표준 dict 목록(날짜 오름차순)으로 변환."""
    import yfinance as yf

    df = yf.download(yf_ticker, period=period, interval="1d", progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        return []
    df = _flatten_columns(df)

    rows: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        close = _safe_float(row.get("Close"))
        if close <= 0:
            continue
        rows.append(
            {
                "date": idx.strftime("%Y-%m-%d"),
                "open": _safe_float(row.get("Open"), close),
                "high": _safe_float(row.get("High"), close),
                "low": _safe_float(row.get("Low"), close),
                "close": close,
                "volume": _safe_int(row.get("Volume")),
            }
        )
    rows.sort(key=lambda r: r["date"])
    return rows


@dataclass
class BackfillResult:
    """백필 실행 요약."""

    requested: int = 0
    succeeded: int = 0
    failed: int = 0
    saved_rows: int = 0
    failed_tickers: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"백필 완료: 성공 {self.succeeded}/{self.requested}종목, "
            f"저장 {self.saved_rows}행, 실패 {self.failed}"
            + (f" ({', '.join(self.failed_tickers[:5])}...)" if self.failed_tickers else "")
        )


class OHLCVBackfiller:
    """종목 리스트의 일봉 이력을 DB에 적재한다."""

    def __init__(self, fetcher: Optional[Fetcher] = None, delay: float = 0.3) -> None:
        self._fetcher: Fetcher = fetcher or default_fetcher
        self._delay = delay

    def fetch_history(self, ticker: str, period: str = "1y") -> Tuple[List[Dict[str, Any]], str]:
        """KOSPI(.KS) → KOSDAQ(.KQ) 순으로 시도해 (행 목록, 사용된 접미사) 반환."""
        for suffix in MARKET_SUFFIXES:
            try:
                rows = self._fetcher(f"{ticker}{suffix}", period)
            except Exception as e:
                logger.debug(f"{ticker}{suffix} 조회 실패: {e}")
                continue
            if rows:
                return rows, suffix
        return [], ""

    @staticmethod
    def to_records(ticker: str, rows: Sequence[Dict[str, Any]]) -> List[Tuple[Any, ...]]:
        """DB 저장용 튜플 목록으로 변환(스키마 순서 고정)."""
        return [
            (
                ticker,
                r["date"],
                r.get("open", 0.0),
                r.get("high", 0.0),
                r.get("low", 0.0),
                r.get("close", 0.0),
                r.get("volume", 0),
            )
            for r in rows
            if r.get("date")
        ]

    async def backfill(
        self,
        tickers: Sequence[str],
        db: Any,
        period: str = "1y",
    ) -> BackfillResult:
        result = BackfillResult(requested=len(tickers))
        for i, ticker in enumerate(tickers, 1):
            rows, suffix = self.fetch_history(ticker, period)
            if not rows:
                result.failed += 1
                result.failed_tickers.append(ticker)
                logger.warning(f"⚠️ [{i}/{len(tickers)}] {ticker}: 이력 없음 (건너뜀)")
                continue

            saved = await db.save_ohlcv_batch(self.to_records(ticker, rows))
            result.succeeded += 1
            result.saved_rows += saved
            logger.info(
                f"✅ [{i}/{len(tickers)}] {ticker}{suffix}: {saved}행 저장 "
                f"({rows[0]['date']} ~ {rows[-1]['date']})"
            )
            if self._delay > 0 and i < len(tickers):
                await asyncio.sleep(self._delay)

        logger.info(result.summary())
        return result


def _resolve_tickers(limit: int) -> List[str]:
    """유니버스에서 상위 limit 종목 코드를 가져온다."""
    from infrastructure.market_data.universe_provider import get_universe

    codes = list(get_universe().keys())
    return codes[:limit]


async def _main(argv: Optional[Sequence[str]] = None) -> int:
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="과거 일봉(OHLCV) 백필 (yfinance)")
    parser.add_argument("--tickers", type=str, default="", help="쉼표 구분 종목코드 (미지정 시 유니버스 상위 N)")
    parser.add_argument("--limit", type=int, default=20, help="유니버스에서 가져올 종목 수 (기본 20)")
    parser.add_argument("--period", type=str, default="1y", help="조회 기간 (yfinance period, 기본 1y)")
    parser.add_argument("--delay", type=float, default=0.3, help="종목 간 대기(초)")
    args = parser.parse_args(argv)

    tickers = (
        [t.strip() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else _resolve_tickers(args.limit)
    )
    if not tickers:
        print("종목이 없습니다.")
        return 2

    from data.db_manager import DatabaseManager

    db = DatabaseManager()
    await db.init_db()
    started = time.time()
    try:
        backfiller = OHLCVBackfiller(delay=args.delay)
        result = await backfiller.backfill(tickers, db, period=args.period)
        print(f"\n{result.summary()}  ({time.time() - started:.1f}s)")
    finally:
        await db.close()
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
