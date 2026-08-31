# -*- coding: utf-8 -*-
"""
orchestrator/sentiment_pipeline.py - 뉴스 감성 분석 파이프라인 v1.0.2

v1.0.2 변경 (Hotfix):
    - CRITICAL: _fetch_news()가 존재하지 않는 NewsCrawler.get_news()를 호출하던
      버그 수정. scanner/deep_analyzer.py의 _get_sentiment_score()에서 실제
      호출부(get_news_with_sentiment(ticker, limit=, cache_seconds=))를 근거로
      확정하고, 튜플 (news_list, sentiment_score) 언패킹으로 수정.
      이 버그는 Session 16에서 NewsCrawler 소스를 확인하지 못한 채 인터페이스를
      가정했던 결과이며, 실제 운영 로그(AttributeError)로 드러난 것을 이번에 수정함.
    - 뉴스 항목의 실제 스키마(dict vs 객체)는 여전히 미확인 상태이므로 두 경우 모두
      방어적으로 처리. 존재 여부가 확인되지 않은 get_news() 폴백 분기는 추가하지 않음
      (근거 없는 추측성 코드를 배제하는 것이 이 프로젝트의 원칙).
    - 신규: _safe_timestamp() 헬퍼 추가. published_at 필드가 datetime/문자열 등
      예측 불가한 타입으로 들어와도 크래시 없이 안전하게 float로 정규화.

v1.0.1 변경:
    - Tuple 타입힌트 typing 미임포트로 인한 NameError 수정.
"""

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.logger import setup_logger
from data.news_sentiment import (
    NewsItem,
    NewsSentimentAnalyzer,
    SentimentResult,
    get_sentiment_analyzer,
)
from observability.auto_trace import TracedService
from observability.tracer import get_tracer

logger = setup_logger("sentiment_pipeline")
trace = get_tracer(__name__)

_MARKET_OPEN_HOUR = 9
_MARKET_CLOSE_HOUR = 15
_MARKET_CLOSE_MINUTE = 30
_INTRADAY_REFRESH_INTERVAL = 900    # 장중 15분
_OFFHOURS_REFRESH_INTERVAL = 3600   # 장외 60분
_CRAWLER_CACHE_SECONDS = 1800       # NewsCrawler 자체 캐시 요청 값 (30분)


def _is_market_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    if now.hour < _MARKET_OPEN_HOUR or now.hour > _MARKET_CLOSE_HOUR:
        return False
    if now.hour == _MARKET_CLOSE_HOUR and now.minute > _MARKET_CLOSE_MINUTE:
        return False
    return True


def _safe_timestamp(value: Any) -> float:
    """다양한 타입을 방어적으로 float 타임스탬프로 변환.

    NewsCrawler.get_news_with_sentiment()가 반환하는 뉴스 항목의 날짜 필드
    실제 타입(datetime 객체, ISO 문자열, epoch 등)이 확인되지 않았으므로,
    무엇이 들어오든 크래시 없이 안전한 기본값(현재 시각)으로 폴백합니다.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    return time.time()


class SentimentPipeline(TracedService):
    """뉴스 감성 분석 파이프라인 (SignalPipeline enrich 훅)."""

    def __init__(
        self,
        news_crawler=None,
        analyzer: Optional[NewsSentimentAnalyzer] = None,
        max_news_per_ticker: int = 20,
    ) -> None:
        self._crawler = news_crawler
        self._analyzer = analyzer or get_sentiment_analyzer()
        self._max_news = max_news_per_ticker
        self._cache: Dict[str, Tuple[SentimentResult, float]] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._refresh_task: Optional[asyncio.Task] = None
        self._active_tickers: List[str] = []

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        logger.info("SentimentPipeline started (background refresh active)")

    async def stop(self) -> None:
        self._running = False
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        logger.info("SentimentPipeline stopped")

    def set_active_tickers(self, tickers: List[str]) -> None:
        self._active_tickers = list(tickers)

    @trace.traced
    async def enrich(self, data: Dict) -> Dict:
        """data['sentiment_score']에 impact_score(0~1)를 주입."""
        ticker = data.get("ticker", "")
        if not ticker or self._crawler is None:
            return {**data, "sentiment_score": 0.5}
        try:
            result = await self._get_or_fetch(ticker)
            return {**data, "sentiment_score": result.impact_score}
        except Exception as e:
            logger.warning(f"Sentiment enrich failed ({ticker}): {e}")
            return {**data, "sentiment_score": 0.5}

    @trace.traced
    async def get_sentiment(self, ticker: str) -> Optional[SentimentResult]:
        try:
            return await self._get_or_fetch(ticker)
        except Exception as e:
            logger.debug(f"get_sentiment failed ({ticker}): {e}")
            return None

    async def _get_or_fetch(self, ticker: str) -> SentimentResult:
        cached = self._cache.get(ticker)
        interval = _INTRADAY_REFRESH_INTERVAL if _is_market_hours() else _OFFHOURS_REFRESH_INTERVAL
        if cached is not None and time.time() - cached[1] < interval:
            return cached[0]

        async with self._lock:
            cached = self._cache.get(ticker)
            if cached is not None and time.time() - cached[1] < interval:
                return cached[0]
            news_list = await self._fetch_news(ticker)
            result = await self._analyzer.analyze(ticker, news_list)
            self._cache[ticker] = (result, time.time())
            return result

    async def _fetch_news(self, ticker: str) -> List[NewsItem]:
        """NewsCrawler에서 뉴스를 가져와 NewsItem 리스트로 변환.

        🔥 v1.0.2 Hotfix: scanner/deep_analyzer.py의 실제 호출부로 확정된
        get_news_with_sentiment(ticker, limit=, cache_seconds=)를 사용합니다.
        반환값은 (news_list, sentiment_score) 튜플이며, 여기서는 news_list만
        사용해 자체 NewsSentimentAnalyzer(한국어 키워드 앙상블)로 재분석합니다.

        뉴스 항목 개별 원소의 실제 스키마(dict/객체)는 data/news_crawler.py
        소스 없이는 확정할 수 없으므로 두 경우 모두 방어적으로 처리합니다.
        존재가 확인되지 않은 get_news() 폴백은 근거 없는 추측이므로 넣지 않았습니다.
        """
        if self._crawler is None:
            return []
        try:
            raw_result = await self._crawler.get_news_with_sentiment(
                ticker, limit=self._max_news, cache_seconds=_CRAWLER_CACHE_SECONDS
            )

            if isinstance(raw_result, tuple) and len(raw_result) >= 1:
                raw_news = raw_result[0]
            else:
                raw_news = raw_result

            if not raw_news:
                return []

            news_items: List[NewsItem] = []
            for item in raw_news:
                if isinstance(item, dict):
                    news_items.append(NewsItem(
                        title=item.get("title", ""),
                        content=item.get("content", item.get("description", item.get("summary", ""))),
                        source=item.get("source", item.get("publisher", "unknown")),
                        published_at=_safe_timestamp(
                            item.get("published_at", item.get("date"))
                        ),
                        url=item.get("url", item.get("link", "")),
                    ))
                elif hasattr(item, "title"):
                    news_items.append(NewsItem(
                        title=getattr(item, "title", ""),
                        content=getattr(item, "content", getattr(item, "description", "")),
                        source=getattr(item, "source", "unknown"),
                        published_at=_safe_timestamp(getattr(item, "published_at", None)),
                        url=getattr(item, "url", ""),
                    ))
                else:
                    logger.debug(f"알 수 없는 뉴스 항목 타입 무시 ({ticker}): {type(item)}")
            return news_items

        except Exception as e:
            logger.debug(f"News fetch failed ({ticker}): {e}")
            return []

    async def _refresh_loop(self) -> None:
        while self._running:
            try:
                interval = _INTRADAY_REFRESH_INTERVAL if _is_market_hours() else _OFFHOURS_REFRESH_INTERVAL
                await asyncio.sleep(interval)
                if not self._active_tickers:
                    continue

                semaphore = asyncio.Semaphore(5)

                async def _refresh_one(t: str) -> None:
                    async with semaphore:
                        try:
                            news = await self._fetch_news(t)
                            result = await self._analyzer.analyze(t, news, force_refresh=True)
                            self._cache[t] = (result, time.time())
                        except Exception as e:
                            logger.debug(f"Refresh failed ({t}): {e}")

                await asyncio.gather(
                    *[_refresh_one(t) for t in self._active_tickers[:100]],
                    return_exceptions=True,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"SentimentPipeline refresh loop error: {e}")
                await asyncio.sleep(60)

    def get_status(self) -> Dict:
        now = time.time()
        fresh = sum(1 for _, ts in self._cache.values() if now - ts < _INTRADAY_REFRESH_INTERVAL)
        return {
            "running": self._running,
            "active_tickers": len(self._active_tickers),
            "cached_tickers": len(self._cache),
            "fresh_cache": fresh,
            "crawler_available": self._crawler is not None,
            "analyzer_cache": self._analyzer.get_cache_stats(),
        }
