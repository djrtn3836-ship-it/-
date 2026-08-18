"""
data/news_crawler.py - v6.2.3 (aiohttp ThreadedResolver 적용)
"""

import os
import re
import json
import asyncio
import aiohttp
import socket
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiohttp.resolver import ThreadedResolver  # 🔥 추가

from core.logger import setup_logger
from core.circuit_breaker import NEWS_CRAWLER_CB
from core.sentiment_analyzer import sentiment_analyzer

logger = setup_logger("news")


class NewsCrawler:
    def __init__(self):
        load_dotenv()
        self.client_id = os.getenv("NAVER_CLIENT_ID")
        self.client_secret = os.getenv("NAVER_CLIENT_SECRET")
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache = {}
        self._cache_time = {}
        
        # 🔥 ThreadedResolver로 DNS 안정화
        self._connector = aiohttp.TCPConnector(
            resolver=ThreadedResolver(),
            use_dns_cache=False,
            family=socket.AF_INET,
            ttl_dns_cache=0
        )

        if not self.client_id or not self.client_secret:
            logger.warning("⚠️ NAVER_CLIENT_ID / SECRET 미설정 → 뉴스 수집 비활성화")

    async def connect(self):
        if self._session is None:
            self._session = aiohttp.ClientSession(connector=self._connector)

    async def disconnect(self):
        if self._session:
            await self._session.close()
            self._session = None

    @NEWS_CRAWLER_CB.protect
    async def fetch_news(self, ticker: str, limit: int = 5) -> Optional[Tuple[List[Dict], float]]:
        if not self.client_id or not self.client_secret:
            return [], 0.0

        if self._session is None:
            await self.connect()

        stock_name_map = {
            "005930": "삼성전자", "000660": "SK하이닉스", "005380": "현대차",
            "035420": "NAVER", "051910": "LG화학", "006400": "삼성SDI",
            "207940": "삼성바이오로직스",
        }
        query = stock_name_map.get(ticker, ticker)

        url = "https://naverapihub.apigw.ntruss.com/search/v1/news"
        headers = {
            "X-NCP-APIGW-API-KEY-ID": self.client_id,
            "X-NCP-APIGW-API-KEY": self.client_secret,
            "Accept": "application/json",
        }
        params = {
            "query": query,
            "display": limit,
            "sort": "date",
            "format": "json",
        }

        try:
            async with self._session.get(url, headers=headers, params=params, timeout=10) as resp:
                if resp.status == 200:
                    try:
                        data = await resp.json()
                    except aiohttp.ContentTypeError:
                        text = await resp.text()
                        try:
                            data = json.loads(text)
                        except json.JSONDecodeError:
                            logger.error(f"❌ JSON 디코딩 실패 ({ticker}): {text[:200]}")
                            return [], 0.0

                    items = data.get("items", [])
                    results, texts_for_sentiment = [], []
                    for item in items:
                        title = re.sub(r'<[^>]+>', '', item.get("title", ""))
                        description = re.sub(r'<[^>]+>', '', item.get("description", ""))
                        results.append({
                            "title": title,
                            "summary": description,
                            "link": item.get("originallink", item.get("link", "")),
                            "pub_date": item.get("pubDate", ""),
                            "source": "naver_api_hub"
                        })
                        texts_for_sentiment.append(title + " " + description)

                    sentiment_score = await sentiment_analyzer.analyze(texts_for_sentiment)
                    logger.debug(f"📰 {ticker} 뉴스 {len(results)}개, 감성: {sentiment_score:+.2f}")
                    return results, sentiment_score
                else:
                    error_text = await resp.text()
                    logger.warning(f"⚠️ 네이버 API 오류 ({resp.status}): {error_text[:200]}")
                    return [], 0.0
        except asyncio.TimeoutError:
            logger.warning(f"⏰ 뉴스 수집 타임아웃 ({ticker})")
            return [], 0.0
        except Exception as e:
            logger.error(f"❌ 뉴스 수집 실패 ({ticker}): {e}")
            return [], 0.0

    # 🔥 이 메서드의 들여쓰기가 빠져 있었음 → 4칸 공백으로 수정
    async def get_news_with_sentiment(self, ticker: str, limit: int = 5, cache_seconds: int = 3600) -> Tuple[List[Dict], float]:
        cache_key = f"{ticker}_{limit}"
        now = datetime.now().timestamp()
        if cache_key in self._cache and (now - self._cache_time.get(cache_key, 0)) < cache_seconds:
            logger.debug(f"📰 [뉴스 캐시] {ticker} 캐시 사용 (남은 시간: {cache_seconds - (now - self._cache_time[cache_key]):.0f}s)")
            return self._cache[cache_key]

        result = await self.fetch_news(ticker, limit)
        if result is None:
            logger.warning(f"⚠️ [뉴스 API] {ticker} 뉴스 수집 실패 → 감성 점수 0.0")
            return [], 0.0
        news, sentiment = result
        self._cache[cache_key] = (news, sentiment)
        self._cache_time[cache_key] = now
        logger.info(f"📰 [뉴스 API] {ticker} 뉴스 {len(news)}개 수집, 감성 점수: {sentiment:+.2f}")
        return news, sentiment

    def get_headlines(self, query: str = "코스피", limit: int = 5) -> List[str]:
        import asyncio
        async def _inner():
            news, _ = await self.get_news_with_sentiment(query, limit=limit)
            return [item.get("title", "") for item in news[:limit]]
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            return asyncio.run_coroutine_threadsafe(_inner(), loop).result()
        else:
            return asyncio.run(_inner())