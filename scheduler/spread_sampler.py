# -*- coding: utf-8 -*-
"""호가 스프레드 실측 샘플러 (S152).

실시간 감시 중인 종목의 최우선 매수/매도 호가를 주기적으로 기록해
비용 모델(validation/cost_model.py)의 스프레드 가정을 실측으로 대체한다.
주문은 하지 않는다(Shadow).
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("spread_sampler")

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "spread_samples.jsonl"


def _best(ob: Dict[str, Any]) -> Optional[tuple[float, float]]:
    bids = ob.get("bids") or []
    asks = ob.get("asks") or []
    if not bids or not asks:
        return None
    try:
        bid = max(float(p) for p, _ in bids if float(p) > 0)
        ask = min(float(p) for p, _ in asks if float(p) > 0)
    except (TypeError, ValueError):
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return bid, ask


async def sample_spreads(
    monitor: Any,
    *,
    out: Path = OUT_PATH,
    limit: int = 60,
    ts: Optional[str] = None,
) -> Dict[str, Any]:
    if monitor is None or not hasattr(monitor, "get_orderbook"):
        return {"sampled": 0, "skipped": True, "reason": "monitor 없음"}

    stamp = ts or datetime.now().isoformat(timespec="seconds")
    tickers: List[str] = list(getattr(monitor, "get_subscribed_tickers", lambda: [])())
    rows: List[Dict[str, Any]] = []
    for t in tickers[:limit]:
        ob = monitor.get_orderbook(str(t))
        if not ob:
            continue
        pair = _best(ob)
        if not pair:
            continue
        bid, ask = pair
        mid = (bid + ask) / 2.0
        rows.append(
            {
                "ts": stamp,
                "ticker": str(t),
                "price": round(mid, 2),
                "bid": bid,
                "ask": ask,
                "spread_bp": round((ask - bid) / mid * 10000.0, 2),
            }
        )

    if rows:
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    spreads = [r["spread_bp"] for r in rows]
    result = {
        "sampled": len(rows),
        "median_bp": round(statistics.median(spreads), 2) if spreads else None,
        "ts": stamp,
    }
    logger.info(f"호가 샘플 {len(rows)}건" + (f" · 중앙 스프레드 {result['median_bp']}bp" if spreads else ""))
    return result


def summarize(path: Path = OUT_PATH, min_samples: int = 20) -> Dict[str, Any]:
    """가격대별 스프레드 요약(비용 모델 입력)."""
    if not path.exists():
        return {"n": 0}
    by_bucket: Dict[str, List[float]] = {}
    all_bp: List[float] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                bp = float(r["spread_bp"])
                px = float(r.get("price") or 0)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            all_bp.append(bp)
            if px < 2000:
                b = "~2천"
            elif px < 5000:
                b = "2~5천"
            elif px < 20000:
                b = "5천~2만"
            elif px < 50000:
                b = "2만~5만"
            else:
                b = "5만+"
            by_bucket.setdefault(b, []).append(bp)
    buckets = {
        b: {"n": len(v), "median_bp": round(statistics.median(v), 2)}
        for b, v in by_bucket.items()
        if len(v) >= min_samples
    }
    return {
        "n": len(all_bp),
        "median_bp": round(statistics.median(all_bp), 2) if all_bp else None,
        "buckets": buckets,
    }
