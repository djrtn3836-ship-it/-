# -*- coding: utf-8 -*-
"""공시 신호 자동 채점 (S144) — 기록된 A/B 이벤트의 실현 초과수익.

기록일 기준 첫 거래일 종가 진입 → 1일/5일 후 종가, KOSPI 대비 초과수익.
결과: data/disclosure_watch_settled.jsonl (append-only, 멱등).
"""
from __future__ import annotations

import bisect
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("disclosure_settle")

ROOT = Path(__file__).resolve().parent.parent
WATCH_LOG = ROOT / "data" / "disclosure_watch_log.jsonl"
SETTLED_PATH = ROOT / "data" / "disclosure_watch_settled.jsonl"
KOSPI = "^KS11"
HORIZON = 5


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


def settle_one(
    event: Dict[str, Any],
    dates: List[str],
    closes: List[float],
    k_dates: List[str],
    k_closes: List[float],
    *,
    horizon: int = HORIZON,
) -> Optional[Dict[str, Any]]:
    """이벤트 1건 채점. 데이터 부족/결측이면 None(대기)."""
    day = str(event.get("date") or "").replace("-", "")
    if len(day) != 8:
        return None
    target = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    base = bisect.bisect_left(dates, target)
    if base >= len(dates) or base + horizon >= len(dates):
        return None
    if dates[base] not in k_dates:
        return None
    k_idx = {d: i for i, d in enumerate(k_dates)}
    j = base + horizon
    if dates[j] not in k_idx:
        return None
    try:
        if closes[base] <= 0 or k_closes[k_idx[dates[base]]] <= 0:
            return None
        ret1 = closes[base + 1] / closes[base] - 1.0
        ret5 = closes[j] / closes[base] - 1.0
        k1 = k_closes[k_idx[dates[base + 1]]] / k_closes[k_idx[dates[base]]] - 1.0
        k5 = k_closes[k_idx[dates[j]]] / k_closes[k_idx[dates[base]]] - 1.0
    except (IndexError, KeyError, ZeroDivisionError):
        return None
    return {
        "date": day,
        "ticker": event.get("ticker"),
        "name": event.get("name"),
        "category": event.get("category"),
        "grade": event.get("grade"),
        "entry_date": dates[base],
        "ret1": round(ret1, 6),
        "excess1": round(ret1 - k1, 6),
        "ret5": round(ret5, 6),
        "excess5": round(ret5 - k5, 6),
        "horizon": horizon,
        "settled_at": datetime.now().isoformat(timespec="seconds"),
    }


def settle(
    *,
    log_path: Path = WATCH_LOG,
    settled_path: Path = SETTLED_PATH,
    db: Any = None,
    logger: Any = None,
) -> Dict[str, Any]:
    """미채점 A/B 이벤트를 채점해 append. 멱등."""
    import asyncio

    from data.db_manager import DatabaseManager

    rows = [r for r in _load_jsonl(log_path) if str(r.get("grade")) in ("A_생존", "B_경계")]
    settled = _load_jsonl(settled_path)
    done = {(str(r.get("date")), str(r.get("ticker")), str(r.get("category"))) for r in settled}
    todo = [r for r in rows if (str(r.get("date")), str(r.get("ticker")), str(r.get("category"))) not in done]

    if not todo:
        return {"settled": 0, "pending": 0, "total_settled": len(settled)}

    async def _run() -> List[Dict[str, Any]]:
        own = db is None
        d = db or DatabaseManager()
        out: List[Dict[str, Any]] = []
        try:
            k_rows = await d.get_ohlcv_range(KOSPI, "2021-10-19", datetime.now().strftime("%Y-%m-%d"))
            k_dates = [str(r["date"])[:10] for r in k_rows]
            k_closes = [float(r["close"]) for r in k_rows]
            by_ticker: Dict[str, List[Dict[str, Any]]] = {}
            for r in todo:
                by_ticker.setdefault(str(r.get("ticker")), []).append(r)
            for ticker, evs in by_ticker.items():
                rows_t = await d.get_ohlcv_range(ticker, "2021-10-19", datetime.now().strftime("%Y-%m-%d"))
                dates = [str(r["date"])[:10] for r in rows_t]
                closes = [float(r["close"]) for r in rows_t]
                for ev in evs:
                    rec = settle_one(ev, dates, closes, k_dates, k_closes)
                    if rec:
                        out.append(rec)
            return out
        finally:
            if own:
                try:
                    await d.close()
                except Exception:
                    pass

    new_rows = asyncio.run(_run())
    if new_rows:
        settled_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settled_path, "a", encoding="utf-8") as f:
            for rec in new_rows:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    pending = len(todo) - len(new_rows)
    if logger:
        logger.info(f"공시 채점: 신규 {len(new_rows)}건 · 대기 {pending}건 · 누적 {len(settled) + len(new_rows)}건")
    return {"settled": len(new_rows), "pending": pending, "total_settled": len(settled) + len(new_rows)}


def disclosure_stats(*, settled_path: Path = SETTLED_PATH) -> List[Dict[str, Any]]:
    """유형·등급별 실현 성적(5일 초과수익 기준)."""
    rows = _load_jsonl(settled_path)
    groups: Dict[tuple, List[float]] = {}
    for r in rows:
        key = (str(r.get("category")), str(r.get("grade")))
        if "excess5" in r:
            groups.setdefault(key, []).append(float(r["excess5"]) * 100.0)
    out = []
    for (cat, grade), vals in sorted(groups.items()):
        n = len(vals)
        out.append(
            {
                "category": cat,
                "grade": grade,
                "n": n,
                "mean5": round(sum(vals) / n, 2),
                "hit5": round(sum(1 for v in vals if v > 0) / n * 100.0, 1),
            }
        )
    return out


def format_stats(rows: List[Dict[str, Any]]) -> str:
    lines = ["공시 신호 실현 성적(5일, KOSPI 대비)"]
    for r in rows:
        lines.append(f"  [{r['grade']}] {r['category']}: n={r['n']} 평균 {r['mean5']:+.2f}% 적중 {r['hit5']:.0f}%")
    return "\n".join(lines)


async def scheduled_disclosure_settle() -> Dict[str, Any]:
    """스케줄러 진입점 — 실패해도 예외를 밖으로 던지지 않는다."""
    try:
        return settle(logger=logger)
    except Exception as e:
        logger.error(f"공시 채점 실패: {e}")
        return {"status": "error", "error": str(e)}
