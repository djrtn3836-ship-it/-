# -*- coding: utf-8 -*-
"""scheduler/supply_backfill.py — 과거 수급(외국인/기관 순매수) 백필.

배경 (2026-10-16, 사용자 승인 → 5년 전체 실행):
    supply_demand 이력이 5일치(10-12~10-16)뿐이라 수급 팩터(쌍끌이·연속성·
    다이버전스) 연구가 불가능했다. ka10060 일봉 조회가 **과거 날짜(dt)로도**
    frgnr_invsr/orgn 순매수를 반환함을 실증(2025-10-16 삼성전자:
    외국인 +561,198 / 기관 +194,533) → 5년 백필.

설계 (무인 장시간 실행 전제 — 약 25~30시간):
    - 주중 날짜만 역순 순회. 응답 봉 날짜가 요청 날짜와 **일치할 때만 저장**
      (휴장일 스킵·오라벨 방지 — 기존 collector와 동일 규약).
    - 요청 1건마다 체크포인트(종목 인덱스 + 다음 날짜) → 재부팅/중단에도 이어서
      재개(멱등, UNIQUE(ticker,date) INSERT OR REPLACE).
    - 날짜당 1회 재시도(2초 후). 연속 실패 5회 → 재로그인 시도, 30회 → 중단.
    - 평일 08:50~15:40(장중)에는 자동 일시정지 — 본 앱의 실시간 감시와 충돌 방지.
    - kiwoom/db/sleep/now 모두 주입 가능(오프라인 테스트).

사용:
    python -m scheduler.supply_backfill              # 5년 전체, 이어서 재개
    python -m scheduler.supply_backfill --years 1    # 기간 축소
    python -m scheduler.supply_backfill --tickers 005930,000660
    python -m scheduler.supply_backfill --reset      # 체크포인트 초기화 후 처음부터
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

from core.logger import setup_logger

logger = setup_logger("supply_backfill")

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_PATH = ROOT / "logs" / "supply_backfill_state.json"
DEFAULT_DELAY = 0.25
RECONNECT_AFTER = 5
MAX_CONSECUTIVE_ERRORS = 30
MARKET_PAUSE_SLEEP = 60.0


def _weekdays_desc(start: date, end: date) -> Iterator[date]:
    """[start, end] 범위의 평일을 최신 → 과거 역순으로."""
    d = end
    while d >= start:
        if d.weekday() < 5:
            yield d
        d -= timedelta(days=1)


def _is_market_hours(now: datetime) -> bool:
    """평일 08:50~15:40 여부(장중 일시정지 판단)."""
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 8 * 60 + 50 <= hm <= 15 * 60 + 40


def _load_checkpoint(path: Path) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_checkpoint(path: Path, state: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        tmp.replace(path)
    except OSError as e:
        logger.warning(f"체크포인트 저장 실패(무시): {e}")


async def backfill(
    kiwoom: Any,
    db: Any,
    tickers: Sequence[str],
    *,
    start_date: date,
    end_date: date,
    delay: float = DEFAULT_DELAY,
    checkpoint_path: Path = CHECKPOINT_PATH,
    max_consecutive_errors: int = MAX_CONSECUTIVE_ERRORS,
    reconnect_after: int = RECONNECT_AFTER,
    market_pause: bool = True,
    sleep: Callable[[float], Any] = asyncio.sleep,
    now_fn: Callable[[], datetime] = datetime.now,
) -> Dict[str, Any]:
    """수급 백필 실행. 통계 dict 반환(중단 포함)."""
    state = _load_checkpoint(checkpoint_path)
    try:
        ti0 = max(0, min(int(state.get("ticker_idx") or 0), len(tickers)))
    except (TypeError, ValueError):
        ti0 = 0

    result: Dict[str, Any] = {
        "saved": 0, "skipped": 0, "errors": 0, "requests": 0, "aborted": False,
    }
    consecutive = 0

    for ti in range(ti0, len(tickers)):
        ticker = str(tickers[ti])
        if ti == ti0 and state.get("next_date"):
            try:
                cur = date.fromisoformat(str(state["next_date"]))
            except ValueError:
                cur = end_date
        else:
            cur = end_date
        if cur > end_date:
            cur = end_date
        ticker_saved_start = result["saved"]

        while cur >= start_date:
            if cur.weekday() >= 5:
                cur -= timedelta(days=1)
                continue
            if market_pause and _is_market_hours(now_fn()):
                logger.info("장중 — 백필 일시정지(60초 후 재확인)")
                await sleep(MARKET_PAUSE_SLEEP)
                continue

            d8 = cur.strftime("%Y%m%d")
            res: Optional[Dict[str, Any]] = None
            for attempt in (1, 2):
                try:
                    res = await kiwoom.request_tr(ticker, "일봉", dt=d8)
                except Exception as e:  # 커넥터 예외 → 오류 응답으로 정규화
                    res = {"error": str(e)[:120]}
                await sleep(delay)
                if res and not res.get("error"):
                    break
                if attempt == 1:
                    await sleep(2.0)  # 일시 오류 재시도(같은 날짜)
            result["requests"] += 1

            if not res or res.get("error"):
                consecutive += 1
                result["errors"] += 1
                if consecutive == reconnect_after:
                    logger.warning(
                        f"연속 {consecutive}회 실패 — 재로그인 시도: {str(res)[:100]}"
                    )
                    try:
                        await kiwoom.connect()
                    except Exception as e:
                        logger.warning(f"재로그인 실패(무시): {e}")
                if consecutive >= max_consecutive_errors:
                    result["aborted"] = True
                    logger.error(
                        f"연속 {consecutive}회 실패 — 백필 중단(API/계정 보호). "
                        f"마지막: {ticker} {cur}. 재실행 시 이어서 진행됩니다."
                    )
                    _save_checkpoint(checkpoint_path, {"ticker_idx": ti, "next_date": cur.isoformat()})
                    return result
            else:
                consecutive = 0
                if str(res.get("date") or "")[:10] == cur.isoformat():
                    f_net, i_net = res.get("foreign_net"), res.get("inst_net")
                    if f_net is not None or i_net is not None:
                        try:
                            n = await db.save_supply_demand([{
                                "ticker": ticker,
                                "date": cur.isoformat(),
                                "foreign_net": f_net,
                                "institution_net": i_net,
                            }])
                            result["saved"] += int(n or 0)
                        except Exception as e:
                            logger.warning(f"[{ticker}] {cur} 저장 실패(무시): {e}")
                else:
                    result["skipped"] += 1  # 휴장일 등 — 응답 봉 날짜 불일치

            cur -= timedelta(days=1)
            _save_checkpoint(checkpoint_path, {
                "ticker_idx": ti, "next_date": cur.isoformat(),
            })
            if result["requests"] % 500 == 0:
                logger.info(
                    f"진행 {result['requests']:,}요청 | {ticker} ({ti + 1}/{len(tickers)}) "
                    f"{cur} | 저장 {result['saved']:,} · 스킵 {result['skipped']:,} "
                    f"· 오류 {result['errors']:,}"
                )

        logger.info(
            f"종목 완료 {ti + 1}/{len(tickers)} {ticker} "
            f"(+{result['saved'] - ticker_saved_start}건)"
        )
        _save_checkpoint(checkpoint_path, {"ticker_idx": ti + 1, "next_date": end_date.isoformat()})

    logger.info(
        f"✅ 백필 완료: 저장 {result['saved']:,}건 / 요청 {result['requests']:,} "
        f"(스킵 {result['skipped']:,} · 오류 {result['errors']:,})"
    )
    return result


def _load_universe() -> List[str]:
    """DB의 수급 수집 종목(현 유니버스 195) 사용. 없으면 ohlcv 종목으로 폴백."""
    db_path = ROOT / "data" / "decisions.db"
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = [r[0] for r in con.execute(
                "SELECT DISTINCT ticker FROM supply_demand ORDER BY ticker"
            )]
            if not rows:
                rows = [r[0] for r in con.execute(
                    "SELECT ticker FROM ohlcv GROUP BY ticker ORDER BY COUNT(*) DESC LIMIT 200"
                )]
        finally:
            con.close()
        return [str(t) for t in rows if t]
    except sqlite3.Error as e:
        logger.error(f"유니버스 로드 실패: {e}")
        return []


async def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="수급 5년 백필 (ka10060 일봉 dt 조회)")
    ap.add_argument("--years", type=float, default=5.0, help="백필 기간(년, 기본 5)")
    ap.add_argument("--tickers", type=str, default="", help="쉼표 구분 종목(기본: DB 유니버스)")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="요청 간 지연(초)")
    ap.add_argument("--reset", action="store_true", help="체크포인트 초기화 후 처음부터")
    args = ap.parse_args(argv)

    if args.reset:
        try:
            CHECKPOINT_PATH.unlink()
            logger.info("체크포인트 초기화 — 처음부터 시작")
        except FileNotFoundError:
            pass

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        tickers = _load_universe()
    if not tickers:
        logger.error("종목이 없습니다 — DB/인자 확인")
        return 1

    end = date.today()
    start = end - timedelta(days=int(365 * args.years))
    logger.info(
        f"수급 백필 시작: {len(tickers)}종목 × 평일 {start}~{end} "
        f"(체크포인트 이어서, 간격 {args.delay}s)"
    )

    from data.db_manager import DatabaseManager
    from data.kiwoom_connector import KiwoomConnectorV512

    kiwoom = KiwoomConnectorV512(rate_limit=3.0)
    if not await kiwoom.connect():
        logger.error("키움 로그인 실패 — 백필 중단")
        return 1

    db = DatabaseManager()
    try:
        res = await backfill(kiwoom, db, tickers, start_date=start, end_date=end, delay=args.delay)
        return 0 if not res.get("aborted") else 2
    finally:
        await db.close()
        for m in ("disconnect", "close"):
            f = getattr(kiwoom, m, None)
            if callable(f):
                try:
                    r = f()
                    if asyncio.iscoroutine(r):
                        await r
                    break
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
