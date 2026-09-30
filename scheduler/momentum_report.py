# -*- coding: utf-8 -*-
"""scheduler/momentum_report.py - 횡단면 모멘텀 참고 신호 + 모의(페이퍼) 추적.

배경:
    `validation/momentum_backtest`에서 횡단면 모멘텀(상대강도)이 롤링 OOS와
    거래비용 반영 후에도 엣지를 보였다(DEVELOPMENT_LOG P2-11).
    생존편향 스트레스(P2-13)에서도 엣지가 유지됨(상폐율 10% 가정 시 Sharpe +0.77).

운영 방식 (2026-10-01 사용자 승인 = 옵션 B):
    매 영업일 아침 모멘텀 상위 종목을 계산해 Telegram으로 **참고 신호**를 보낸다.
    - 주문/포지션은 생성하지 않는다(자동매매 아님).
    - 픽을 DB(`momentum_paper`)에 기록해 **모의 추적**하고, 보유기간(20거래일)이
      지나면 실제 수익률을 확정해 리포트에 누적 성과로 함께 보여준다.

검증 설정(고정): lookback=120일, top_k=20종목, hold=20거래일

사용:
    python -m scheduler.momentum_report
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.logger import setup_logger

logger = setup_logger("momentum_report")

LOOKBACK = 120
TOP_K = 20
HOLD_DAYS = 20
MIN_BARS = LOOKBACK + 5


async def compute_momentum_picks(
    db: Any,
    tickers: Sequence[str],
    names: Optional[Dict[str, str]] = None,
    lookback: int = LOOKBACK,
    top_k: int = TOP_K,
    end_date: Optional[str] = None,
) -> List[Tuple[str, str, float]]:
    """모멘텀 상위 종목 (코드, 종목명, lookback 수익률) 목록."""
    from validation.momentum_backtest import (
        build_price_panel,
        load_price_series,
        select_top_k,
    )

    end = end_date or datetime.now().strftime("%Y-%m-%d")
    # lookback 거래일 확보를 위한 넉넉한 달력 범위(주말/휴일 감안 ×1.6)
    start = (date.fromisoformat(end) - timedelta(days=int(lookback * 1.6) + 20)).isoformat()

    series, dates = await load_price_series(db, tickers, start, end, min_bars=MIN_BARS)
    if len(dates) <= lookback or not series:
        logger.warning(f"모멘텀 계산 불가: 날짜 {len(dates)}일, 종목 {len(series)}개")
        return []

    panel = build_price_panel(series, dates)
    i = len(dates) - 1
    base_row, cur_row = panel[i - lookback], panel[i]
    momentum = {
        t: cur_row[t] / base_row[t] - 1.0
        for t in cur_row
        if base_row.get(t) and base_row[t] > 0
    }
    if not momentum:
        return []

    name_map = names or {}
    picks = select_top_k(momentum, top_k)
    return [(t, name_map.get(t, t), momentum[t]) for t in picks]


async def record_today_picks(
    db: Any,
    picks: Sequence[Tuple[str, str, float]],
    reference_date: Optional[str] = None,
) -> int:
    """픽을 모의 추적 테이블에 기록한다(진입가 = 마지막 거래일 종가).

    아침 실행 시 당일 종가가 아직 없으므로, 각 종목의 **마지막 가용 봉**을
    기준일/진입가로 사용한다(전일 종가 기준 진입으로 간주).

    Returns:
        기록된 픽 수
    """
    if not picks:
        return 0

    end = reference_date or datetime.now().strftime("%Y-%m-%d")
    start = (date.fromisoformat(end) - timedelta(days=30)).isoformat()
    rows: List[Dict[str, Any]] = []

    for rank, (code, _name, ret) in enumerate(picks, 1):
        try:
            bars = await db.get_ohlcv_range(code, start, end)
        except Exception as e:
            logger.warning(f"모의 픽 기준가 조회 실패 [{code}]: {e}")
            continue
        if not bars:
            continue
        last = bars[-1]
        entry = float(last["close"]) if last.get("close") else 0.0
        if entry <= 0:
            continue
        rows.append({
            "pick_date": str(last["date"]),
            "ticker": code,
            "rank": rank,
            "momentum_ret": float(ret),
            "entry_price": entry,
        })

    if rows:
        try:
            await db.save_momentum_picks(rows)
        except Exception as e:
            logger.warning(f"모의 픽 저장 실패(무시): {e}")
            return 0
    return len(rows)


async def evaluate_due_picks(db: Any, hold_days: int = HOLD_DAYS) -> int:
    """보유기간이 지난 모의 픽의 성과를 확정한다.

    Returns:
        이번에 평가 완료된 건수
    """
    try:
        pending = await db.get_momentum_paper_pending()
    except Exception as e:
        logger.warning(f"모의 픽 조회 실패(무시): {e}")
        return 0

    today = datetime.now().strftime("%Y-%m-%d")
    done = 0
    for row in pending:
        try:
            bars = await db.get_ohlcv_range(row["ticker"], row["pick_date"], today)
            if len(bars) <= hold_days:
                continue  # 아직 보유기간 미경과
            entry = float(row["entry_price"])
            exit_price = float(bars[hold_days]["close"])
            if entry <= 0 or exit_price <= 0:
                continue
            await db.update_momentum_paper_result(
                int(row["id"]), exit_price, exit_price / entry - 1.0
            )
            done += 1
        except Exception as e:
            logger.warning(f"모의 픽 평가 실패 [{row.get('ticker')}]: {e}")

    if done:
        logger.info(f"모멘텀 모의 추적: {done}건 평가 완료")
    return done


def format_report(
    picks: Sequence[Tuple[str, str, float]],
    lookback: int = LOOKBACK,
    stats: Optional[Dict[str, Any]] = None,
) -> str:
    """Telegram용 참고 신호 + 모의 추적 성과 텍스트."""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"📈 <b>모멘텀 참고 신호</b> ({today})",
        f"<i>상대강도(최근 {lookback}거래일) 상위 {len(picks)}종목 · "
        f"{HOLD_DAYS}거래일 보유 가정</i>",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not picks:
        lines.append("⚠️ 데이터 부족으로 산출 불가")
    else:
        for rank, (code, name, ret) in enumerate(picks, 1):
            lines.append(f"{rank:>2}. <b>{name}</b> ({code})  {ret:+.1%}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    if stats and stats.get("evaluated"):
        lines.append("<b>모의 추적 성과</b> (실거래 아님)")
        lines.append(
            f"평가 {stats['evaluated']}건 · 승률 {stats['win_rate']:.1%} · "
            f"평균 {stats['avg_return']:+.1%} · 중앙 {stats['median_return']:+.1%}"
        )
        lines.append(
            f"최고 {stats['best']:+.1%} · 최저 {stats['worst']:+.1%}"
            + (f" · 대기 {stats['pending']}건" if stats.get("pending") else "")
        )
    else:
        lines.append("<i>모의 추적: 아직 평가 완료된 픽이 없습니다(축적 중)</i>")

    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>⚠️ 참고용 모의 신호 — 주문/포지션 아님 · 생존편향 스트레스 검증됨</i>")
    return "\n".join(lines)


async def send_momentum_report() -> None:
    """스케줄러 진입점: 픽 계산 → 모의 기록/평가 → Telegram 전송."""
    from data.db_manager import DatabaseManager
    from infrastructure.market_data.universe_provider import get_universe
    from report.telegram_sender import TelegramSender

    universe = get_universe()
    db = DatabaseManager()
    picks: List[Tuple[str, str, float]] = []
    stats: Optional[Dict[str, Any]] = None
    try:
        # 1) 보유기간이 지난 픽 성과 확정
        await evaluate_due_picks(db)
        # 2) 오늘의 픽 계산 + 모의 기록
        picks = await compute_momentum_picks(db, list(universe.keys()), names=universe)
        await record_today_picks(db, picks)
        # 3) 누적 성과
        stats = await db.get_momentum_paper_stats()
    except Exception as e:
        logger.error(f"모멘텀 리포트 계산 실패: {e}")
    finally:
        try:
            await db.close()
        except Exception:
            pass

    try:
        await TelegramSender().send_raw(format_report(picks, stats=stats))
    except Exception as e:
        logger.warning(f"모멘텀 리포트 전송 실패: {e}")
    logger.info(f"모멘텀 리포트 완료: {len(picks)}종목 (모의 추적 {stats})")


async def _main() -> int:
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    from data.db_manager import DatabaseManager
    from infrastructure.market_data.universe_provider import get_universe

    universe = get_universe()
    db = DatabaseManager()
    await db.init_db()
    try:
        await evaluate_due_picks(db)
        picks = await compute_momentum_picks(db, list(universe.keys()), names=universe)
        await record_today_picks(db, picks)
        stats = await db.get_momentum_paper_stats()
    finally:
        await db.close()

    text = format_report(picks, stats=stats)
    for tag in ("<b>", "</b>", "<i>", "</i>"):
        text = text.replace(tag, "")
    print(text)
    return 0 if picks else 1


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(_main()))
