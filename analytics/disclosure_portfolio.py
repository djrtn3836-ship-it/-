# -*- coding: utf-8 -*-
"""공시 전략 모의 포트폴리오 (S146) — 백테스트 규칙의 전방 검증.

실제 주문 없음. 매일 16:45:
  ① 오늘 A급 공시 신호(14:50 스캔 기록)를 종가로 가상 매수
  ② 보유기간(자사주·무상증자 5일, 소각·합병 1일) 도달 시 종가 청산
  ③ 평가액 기록(시작 1.0) → 누적 성과·승률
비용: 왕복 0.3%(매수 0.15% + 매도 0.15%) — 백테스트와 동일.
"""
from __future__ import annotations

import bisect
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("disclosure_portfolio")

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "disclosure_portfolio_state.json"
WATCH_LOG = ROOT / "data" / "disclosure_watch_log.jsonl"
KOSPI = "^KS11"
COST = 0.0015
MAX_POS = 5
START_CASH = 1.0
HOLD_DAYS = {"자사주매입": 5, "무상증자": 5, "주식소각": 1, "합병": 1}
VOL_TARGET = 0.15          # 목표 연변동성(S150 역변동성 실증)
VOL_LOOKBACK = 20
VOL_SCALE_MIN, VOL_SCALE_MAX = 0.5, 1.5


def _ann_vol(series: Dict[str, Any], ticker: str) -> Optional[float]:
    entry = series.get(ticker)
    if not entry:
        return None
    closes = entry[2]
    if len(closes) < VOL_LOOKBACK + 1:
        return None
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(len(closes) - VOL_LOOKBACK, len(closes))]
    sd = statistics.pstdev(rets)
    return sd * (252 ** 0.5) if sd > 0 else None


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _load_state(path: Path = STATE_PATH) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"start": None, "cash": START_CASH, "positions": [], "closed": [], "equity": {}}


def _save_state(state: Dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _dates_for(series: Dict[str, Any], ticker: str) -> List[str]:
    return series.get(ticker, ([],))[0]


def _price_on(series: Dict[str, Any], ticker: str, day: str) -> Optional[float]:
    entry = series.get(ticker)
    if not entry:
        return None
    dates, closes = entry[0], entry[2]
    i = bisect.bisect_right(dates, day) - 1
    if i < 0:
        return None
    return closes[i]


def run_portfolio(
    *,
    today: Optional[str] = None,
    state_path: Path = STATE_PATH,
    watch_log: Path = WATCH_LOG,
    db: Any = None,
    max_pos: int = MAX_POS,
    sizing: str = "equal",
    logger: Any = None,
) -> Dict[str, Any]:
    """하루치 모의 운영(청산 → 신규 → 평가). 멱등(같은 날 재실행 안전)."""
    import asyncio

    from data.db_manager import DatabaseManager

    day = today or datetime.now().strftime("%Y-%m-%d")
    state = _load_state(state_path)
    if state.get("start") is None:
        state["start"] = day

    # 오늘 A급 신호(공시 감시 기록) — 보유기간 정의된 유형만, 15:00 이전 스캔분(종가 진입 가능)
    def _before_close(r: Dict[str, Any]) -> bool:
        stamp = str(r.get("logged_at") or "")
        if len(stamp) >= 16 and "T" in stamp:
            return stamp[11:16] <= "15:00"
        return True

    todays = [
        r for r in _load_jsonl(watch_log)
        if str(r.get("date")) == day.replace("-", "")
        and str(r.get("grade")).startswith("A")
        and r.get("category") in HOLD_DAYS
        and _before_close(r)
    ]

    tickers = {str(p["ticker"]) for p in state.get("positions", [])} | {str(r["ticker"]) for r in todays}
    start = "2021-10-19"

    async def _load():
        own = db is None
        d = db or DatabaseManager()
        series: Dict[str, Any] = {}
        try:
            for t in sorted(tickers):
                r = await d.get_ohlcv_range(t, start, day)
                rows = [{"date": str(x["date"])[:10], "close": float(x["close"])} for x in (r or [])]
                if not rows or rows[-1]["date"] < day:
                    # 유니버스 밖 종목(일봉 미수집) → 당일 종가 온디맨드 보충
                    try:
                        from scheduler.ohlcv_backfill import OHLCVBackfiller

                        fetched, _ = await asyncio.to_thread(
                            OHLCVBackfiller(delay=0.0).fetch_history, t, "1mo"
                        )
                        merged = {x["date"]: x for x in rows}
                        for x in fetched:
                            if x.get("date") and float(x.get("close") or 0) > 0:
                                merged[x["date"]] = {"date": x["date"], "close": float(x["close"])}
                        rows = [merged[k] for k in sorted(merged) if k <= day]
                    except Exception as e:
                        logger.debug(f"{t} 보충 시세 실패(무시): {e}")
                if not rows:
                    continue
                dates = [x["date"] for x in rows]
                closes = [x["close"] for x in rows]
                series[t] = (dates, [0.0] * len(dates), closes, [0.0] * len(dates))
            return series
        finally:
            if own:
                try:
                    await d.close()
                except Exception:
                    pass

    series = asyncio.run(_load())
    cash = float(state.get("cash", START_CASH))
    positions: List[Dict[str, Any]] = list(state.get("positions", []))
    closed: List[Dict[str, Any]] = list(state.get("closed", []))
    opened = 0
    sold = 0

    # ① 청산 — 진입 후 보유 거래일(hold) 경과 여부로 판정
    still: List[Dict[str, Any]] = []
    for p in positions:
        dates = _dates_for(series, str(p["ticker"]))
        if not dates:
            still.append(p)
            continue
        i = bisect.bisect_left(dates, str(p["entry_date"]))
        j = bisect.bisect_left(dates, day)
        if j - i >= int(p.get("hold", 5)):
            px = _price_on(series, str(p["ticker"]), day) or float(p["entry_price"])
            cash += float(p["shares"]) * px * (1.0 - COST)
            closed.append({**p, "exit_date": day, "exit_price": px})
            sold += 1
        else:
            still.append(p)
    positions = still

    # ② 신규(종가 매수, 최대 max_pos) — 같은 날 재실행 멱등 + 백테스트와 동일한 20거래일 중복 제한
    held_keys = {(str(p["ticker"]), str(p["entry_date"])) for p in positions} | {
        (str(c["ticker"]), str(c["entry_date"])) for c in closed
    }
    last_entry: Dict[tuple, str] = {}
    for p in positions + closed:
        key = (str(p["ticker"]), str(p.get("category")))
        d = str(p.get("entry_date"))
        if d > last_entry.get(key, ""):
            last_entry[key] = d
    for r in todays:
        if len(positions) >= max_pos:
            break
        t = str(r["ticker"])
        if (t, day) in held_keys:
            continue
        if any(p["ticker"] == t for p in positions):
            continue
        prev_day = last_entry.get((t, str(r["category"])))
        if prev_day:
            dates = _dates_for(series, t)
            if dates and bisect.bisect_left(dates, day) - bisect.bisect_left(dates, prev_day) < 20:
                continue
        px = _price_on(series, t, day)
        if not px or px <= 0:
            continue
        eq = cash + sum(
            float(p["shares"]) * (_price_on(series, str(p["ticker"]), day) or float(p["entry_price"]))
            for p in positions
        )
        notional = eq / max_pos
        vol = _ann_vol(series, t)
        scale = 1.0
        if sizing == "inverse_vol" and vol:
            scale = max(VOL_SCALE_MIN, min(VOL_SCALE_MAX, VOL_TARGET / vol))
            notional *= scale
        if notional > cash:
            notional = cash
        if notional <= 0:
            continue
        shares = notional / (px * (1.0 + COST))
        cash -= notional
        dates_t = _dates_for(series, t)
        price_date = dates_t[bisect.bisect_right(dates_t, day) - 1] if dates_t else ""
        if price_date != day:
            logger.warning(f"{t} 진입가가 당일 종가가 아님({price_date}) — 평가 왜곡 가능")
        positions.append(
            {
                "ticker": t,
                "name": r.get("name", ""),
                "category": r["category"],
                "entry_date": day,
                "entry_price": px,
                "price_date": price_date,
                "shares": shares,
                "vol": round(vol, 4) if vol else None,
                "scale": round(scale, 2),
                "hold": HOLD_DAYS[str(r["category"])],
            }
        )
        opened += 1

    # ③ 평가
    equity = cash + sum(
        float(p["shares"]) * (_price_on(series, str(p["ticker"]), day) or float(p["entry_price"]))
        for p in positions
    )
    state.update({"cash": cash, "positions": positions, "closed": closed, "last_run": day})
    state.setdefault("equity", {})[day] = round(equity, 6)
    _save_state(state, state_path)

    wins = sum(1 for c in closed if float(c.get("exit_price", 0)) > float(c.get("entry_price", 0)))
    summary = {
        "date": day,
        "equity": round(equity, 4),
        "total_pct": round((equity / START_CASH - 1.0) * 100.0, 2),
        "opened": opened,
        "sold": sold,
        "positions": len(positions),
        "closed": len(closed),
        "win_rate": round(wins / len(closed) * 100.0, 1) if closed else 0.0,
        "cash": round(cash, 4),
    }
    if logger:
        logger.info(
            f"모의포트폴리오 {day}: 평가 {equity:.4f} ({summary['total_pct']:+.2f}%) "
            f"· 신규 {opened} · 청산 {sold} · 보유 {len(positions)}"
        )
    return summary


def load_summary(state_path: Path = STATE_PATH) -> Dict[str, Any]:
    """브리핑/대시보드용 요약(파일 전용)."""
    state = _load_state(state_path)
    eq = state.get("equity") or {}
    if not eq:
        return {}
    last_day = max(eq)
    closed = state.get("closed") or []
    wins = sum(1 for c in closed if float(c.get("exit_price", 0)) > float(c.get("entry_price", 0)))
    return {
        "start": state.get("start"),
        "last_day": last_day,
        "equity": eq[last_day],
        "total_pct": round((eq[last_day] / START_CASH - 1.0) * 100.0, 2),
        "positions": len(state.get("positions") or []),
        "closed": len(closed),
        "win_rate": round(wins / len(closed) * 100.0, 1) if closed else 0.0,
    }


async def scheduled_disclosure_portfolio() -> Dict[str, Any]:
    """스케줄러 진입점 — 실패해도 예외를 밖으로 던지지 않는다."""
    try:
        return run_portfolio(sizing="inverse_vol", logger=logger)
    except Exception as e:
        logger.error(f"모의포트폴리오 실패: {e}")
        return {"status": "error", "error": str(e)}
