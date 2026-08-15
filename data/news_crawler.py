"""
data/news_crawler.py - v6.0.0 (네이버 검색 API + 감성 분석 연동)
- 네이버 Open API 사용 (Client ID/Secret 필요)
- 수집된 뉴스의 감성 점수까지 함께 반환
"""

import os
import json
import asyncio
import aiohttp
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from dotenv import load_dotenv

from core.logger import setup_logger
from core.circuit_breaker import NEWS_CRAWLER_CB
from core.sentiment_analyzer import sentiment_analyzer

logger = setup_logger("news")

class NewsCrawler:
    """뉴스 크롤러 (네이버 Open API)"""
    
    def __init__(self):
        load_dotenv()
        self.client_id = os.getenv("NAVER_CLIENT_ID")
        self.client_secret = os.getenv("NAVER_CLIENT_SECRET")
        self._session: Optional[aiohttp.ClientSession] = None
        
        if not self.client_id or not self.client_secret:
            logger.warning("⚠️ NAVER_CLIENT_ID / SECRET 미설정 → 뉴스 수집 비활성화")
    
    async def connect(self):
        if self._session is None:
            self._session = aiohttp.ClientSession()
    
    async def disconnect(self):
        if self._session:
            await self._session.close()
            self._session = None
    
    @NEWS_CRAWLER_CB.protect
    async def fetch_news(self, ticker: str, limit: int = 5) -> Tuple[List[Dict], float]:
        """
        종목 관련 뉴스 수집 + 평균 감성 점수 반환
        Returns: (news_list, sentiment_score)
        """
        if not self.client_id or not self.client_secret:
            return [], 0.0
        
        if self._session is None:
            await self.connect()
        
        # 종목명 매핑 (간단한 예시)
        stock_name_map = {
            "005930": "삼성전자",
            "000660": "SK하이닉스",
            "005380": "현대차",
            # 필요 시 확장
        }
        query = stock_name_map.get(ticker, ticker)
        
        url = "https://openapi.naver.com/v1/search/news.json"
        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret
        }
        params = {
            "query": query,
            "display": limit,
            "sort": "date"
        }
        
        try:
            async with self._session.get(url, headers=headers, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                    
                    results = []
                    texts_for_sentiment = []
                    for item in items:
                        # HTML 태그 제거
                        title = re.sub(r'<[^>]+>', '', item.get("title", ""))
                        description = re.sub(r'<[^>]+>', '', item.get("description", ""))
                        results.append({
                            "title": title,
                            "summary": description,
                            "link": item.get("link", ""),
                            "pub_date": item.get("pubDate", ""),
                            "source": "naver_api"
                        })
                        texts_for_sentiment.append(title + " " + description)
                    
                    # 🔥 감성 분석 수행
                    sentiment_score = await sentiment_analyzer.analyze(texts_for_sentiment)
                    logger.debug(f"📰 {ticker} 뉴스 {len(results)}개, 감성: {sentiment_score:+.2f}")
                    return results, sentiment_score
                else:
                    logger.warning(f"⚠️ 네이버 API 오류: {resp.status} - {await resp.text()}")
                    return [], 0.0
        except Exception as e:
            logger.error(f"❌ 뉴스 수집 실패 ({ticker}): {e}")
            return [], 0.0

    # 🔥 종목별 캐시 (성능 최적화)
    _cache = {}
    _cache_time = {}
    
    async def get_news_with_sentiment(self, ticker: str, limit: int = 5, cache_seconds: int = 3600) -> Tuple[List[Dict], float]:
        """캐시 적용된 뉴스 + 감성 점수 조회"""
        cache_key = f"{ticker}_{limit}"
        now = datetime.now().timestamp()
        
        if cache_key in self._cache and (now - self._cache_time.get(cache_key, 0)) < cache_seconds:
            return self._cache[cache_key]
        
        news, sentiment = await self.fetch_news(ticker, limit)
        self._cache[cache_key] = (news, sentiment)
        self._cache_time[cache_key] = now
        return news, sentiment