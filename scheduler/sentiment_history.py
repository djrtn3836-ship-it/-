# -*- coding: utf-8 -*-
"""감성 이력 축적 (S148) — 종목별 일별 뉴스 감성 스냅샷.

배경: 감성 팩터는 이력이 없어 검증이 불가능했다. 매일 18:20에 유니버스(최대 200종목)의
      뉴스 감성을 계산해 data/sentiment_history.jsonl 에 1회 기록한다(날짜·종목 중복 방지).
      뉴스가 없는 종목은 '중립'으로 위장하지 않고 기록을 생략한다.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("sentiment_history")

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "data" / "sentiment_history.jsonl"
MAX_TICKERS = 200
NEWS_LIMIT = 5


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


def _parse_pub_date(value: Any) -> float:
    from orchestrator.sentiment_pipeline import _parse_pub_date as parse

    return parse(value)


async def collect_daily(
    tickers: Optional[List[str]] = None,
    *,
    limit: int = NEWS_LIMIT,
    max_tickers: int = MAX_TICKERS,
    log_path: Path = LOG_PATH,
    crawler: Any = None,
    analyzer: Any = None,
    sleep: float = 0.15,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    """유니버스 감성 스냅샷 1회 수집(멱등). 뉴스 없음/실패는 건너뜀."""
    from data.news_crawler import NewsCrawler
    from data.news_sentiment import NewsItem, get_sentiment_analyzer
    from infrastructure.market_data.universe_provider import get_universe

    day = today or date.today().isoformat()
    names = get_universe()
    target = list(tickers) if tickers is not None else list(names)
    target = target[:max_tickers]

    existing = {(str(r.get("date")), str(r.get("ticker"))) for r in _load_jsonl(log_path)}
    todo = [t for t in target if (day, t) not in existing]
    if not todo:
        return {"date": day, "collected": 0, "skipped": 0, "pending": 0}

    own_crawler = crawler is None
    c = crawler or NewsCrawler()
    a = analyzer or get_sentiment_analyzer()
    rows: List[Dict[str, Any]] = []
    skipped = errors = 0
    try:
        if own_crawler and hasattr(c, "connect"):
            await c.connect()
        for t in todo:
            try:
                raw, _ = await c.get_news_with_sentiment(t, limit=limit)
            except Exception as e:
                errors += 1
                logger.debug(f"{t} 뉴스 조회 실패(무시): {e}")
                continue
            if not raw:
                skipped += 1
                continue
            items = [
                NewsItem(
                    title=str(x.get("title") or ""),
                    content=str(x.get("summary") or ""),
                    source=str(x.get("source") or "naver_api_hub"),
                    published_at=_parse_pub_date(x.get("pub_date")),
                    url=str(x.get("link") or ""),
                )
                for x in raw
                if isinstance(x, dict)
            ]
            if not items:
                skipped += 1
                continue
            try:
                res = await a.analyze(t, items)
            except Exception as e:
                errors += 1
                logger.debug(f"{t} 감성 분석 실패(무시): {e}")
                continue
            rows.append(
                {
                    "date": day,
                    "ticker": t,
                    "name": names.get(t, ""),
                    "score": round(float(res.score), 4),
                    "label": str(getattr(res.label, "value", res.label)),
                    "confidence": round(float(res.confidence), 3),
                    "news": int(res.news_count),
                    "pos": int(res.positive_count),
                    "neg": int(res.negative_count),
                }
            )
            if sleep:
                await asyncio.sleep(sleep)
    finally:
        if own_crawler and hasattr(c, "disconnect"):
            try:
                await c.disconnect()
            except Exception:
                pass

    if rows:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info(f"감성 이력 {day}: 수집 {len(rows)} · 뉴스없음 {skipped} · 오류 {errors}")
    return {"date": day, "collected": len(rows), "skipped": skipped, "errors": errors,
            "pending": len(todo) - len(rows) - skipped - errors}


def summary(log_path: Path = LOG_PATH) -> Dict[str, Any]:
    """누적 현황(브리핑/대시보드용)."""
    rows = _load_jsonl(log_path)
    if not rows:
        return {}
    days = sorted({str(r.get("date")) for r in rows})
    tickers = {str(r.get("ticker")) for r in rows}
    return {"days": len(days), "first": days[0], "last": days[-1], "tickers": len(tickers), "rows": len(rows)}


async def scheduled_sentiment_history() -> Dict[str, Any]:
    try:
        return await collect_daily()
    except Exception as e:
        logger.error(f"감성 이력 수집 실패: {e}")
        return {"status": "error", "error": str(e)}
