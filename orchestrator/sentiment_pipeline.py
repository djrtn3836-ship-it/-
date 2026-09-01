# -*- coding: utf-8 -*-
"""
orchestrator/sentiment_pipeline.py - 뉴스 감성 분석 파이프라인 v1.0.3

v1.0.3 변경 (data/news_crawler.py 실제 소스 확인 완료):
    - CRITICAL: 실제 응답 스키마 확정 (dict, 키: title/summary/link/pub_date/source).
      이전 버전에서 사용하던 content/description/published_at 키는 존재하지 않아
      뉴스 본문이 항상 빈 문자열로 들어가고 있었음.
    - pub_date(RFC 2822 문자열, 예: "Wed, 01 Jan 2025 12:00:00 +0900")를
      email.utils.parsedate_to_datetime()로 안전하게 파싱, 실패 시 현재 시각 폴백.
    - 방어적 dict/객체 이중 분기 제거 (스키마가 dict로 확정됨에 따라 불필요).
    - 미사용 typing.Any 임포트 제거 (pyflakes 0 유지).
    - 발견했으나 미적용(의도적 보수적 결정): NewsCrawler.get_news_with_sentiment()는
      내부적으로 core/sentiment_analyzer.py(소스 미확인)로 이미 감성 점수를
      계산해 반환하지만, 그 값을 신뢰하기 전까지는 Session 16의 검증된
      NewsSentimentAnalyzer(68개 단위 테스트)만 판단에 사용합니다.
      크롤러 자체 점수는 디버그 로그로만 노출합니다.
    - 현재 .env에 NAVER_CLIENT_ID/SECRET이 없어 NewsCrawler는 항상 비활성
      ([], 0.0 즉시 반환)이므로, 이 기능은 현재 항상 중립(0.5)을 안전하게 반환합니다.

v1.0.2 이전 이력: get_news()→get_news_with_sentiment() 핫픽스(v1.0.2),
Tuple 임포트 수정(v1.0.1), Session 16 최초 구현(v1.0.0).
"""

import asyncio
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple

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


def _parse_pub_date(pub_date_str: Optional[str]) -> float:
    """네이버 뉴스 API의 pub_date(RFC 2822 문자열)를 epoch float로 변환.

    예: "Wed, 01 Jan 2025 12:00:00 +0900"
    파싱 실패 또는 빈 문자열이면 현재 시각으로 안전하게 폴백합니다.
    """
    if not pub_date_str:
        return time.time()
    try:
        dt = parsedate_to_datetime(pub_date_str)
        return dt.timestamp()
    except (TypeError, ValueError, OverflowError):
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

        확정된 실제 스키마 (data/news_crawler.py v6.3.0 소스 확인):
            dict, 키: title / summary / link / pub_date / source
        """
        if self._crawler is None:
            return []
        try:
            raw_news, crawler_sentiment = await self._crawler.get_news_with_sentiment(
                ticker, limit=self._max_news, cache_seconds=_CRAWLER_CACHE_SECONDS
            )
            if not raw_news:
                return []

            if crawler_sentiment:
                logger.debug(
                    "NewsCrawler 자체 감성점수(참고용, 판단에 미반영): "
                    "%s=%.3f (core/sentiment_analyzer.py 미검증)",
                    ticker, crawler_sentiment,
                )

            news_items: List[NewsItem] = []
            for item in raw_news:
                if not isinstance(item, dict):
                    logger.debug(f"알 수 없는 뉴스 항목 타입 무시 ({ticker}): {type(item)}")
                    continue
                news_items.append(NewsItem(
                    title=item.get("title", ""),
                    content=item.get("summary", ""),
                    source=item.get("source", "naver_api_hub"),
                    published_at=_parse_pub_date(item.get("pub_date")),
                    url=item.get("link", ""),
                ))
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
