# -*- coding: utf-8 -*-
"""scheduler/momentum_report.py - 횡단면 모멘텀 관찰 리포트 (매매 아님).

배경:
    `validation/momentum_backtest`에서 횡단면 모멘텀(상대강도)이 롤링 OOS와
    거래비용 반영 후에도 엣지를 보였다(DEVELOPMENT_LOG P2-11).
    본 모듈은 그 전략을 **관찰 전용**으로 실시스템에 연결한다:
    매 영업일 아침 모멘텀 상위 종목을 계산해 Telegram으로 보낸다(주문/포지션 없음).

검증 설정(고정): lookback=120일, top_k=20종목
주의: 유니버스가 현재 상장 종목 기준이라 생존편향이 존재한다 → 관찰용으로만 사용.

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


def format_report(picks: Sequence[Tuple[str, str, float]], lookback: int = LOOKBACK) -> str:
    """Telegram용 관찰 리포트 텍스트."""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"📈 <b>모멘텀 관찰 리포트</b> ({today})",
        f"<i>상대강도(최근 {lookback}거래일 수익률) 상위 {len(picks)}종목 · 매매 아님</i>",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not picks:
        lines.append("⚠️ 데이터 부족으로 산출 불가")
    else:
        for rank, (code, name, ret) in enumerate(picks, 1):
            lines.append(f"{rank:>2}. <b>{name}</b> ({code})  {ret:+.1%}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>⚠️ 유니버스 생존편향 존재 · 참고용</i>")
    return "\n".join(lines)


async def send_momentum_report() -> None:
    """스케줄러 진입점: 모멘텀 상위 종목을 계산해 Telegram으로 전송."""
    from data.db_manager import DatabaseManager
    from infrastructure.market_data.universe_provider import get_universe
    from report.telegram_sender import TelegramSender

    universe = get_universe()
    db = DatabaseManager()
    try:
        picks = await compute_momentum_picks(db, list(universe.keys()), names=universe)
    except Exception as e:
        logger.error(f"모멘텀 리포트 계산 실패: {e}")
        picks = []
    finally:
        try:
            await db.close()
        except Exception:
            pass

    try:
        await TelegramSender().send_raw(format_report(picks))
    except Exception as e:
        logger.warning(f"모멘텀 리포트 전송 실패: {e}")
    logger.info(f"모멘텀 리포트 완료: {len(picks)}종목")


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
        picks = await compute_momentum_picks(db, list(universe.keys()), names=universe)
    finally:
        await db.close()

    print(format_report(picks).replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
    return 0 if picks else 1


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(_main()))
