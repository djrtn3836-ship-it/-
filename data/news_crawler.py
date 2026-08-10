"""
News Crawler v5.1.2
국내 뉴스 수집 (네이버/다음)
"""

import asyncio
import aiohttp
import re
from typing import List, Dict, Optional
from datetime import datetime, timedelta

from core.logger import setup_logger
from core.circuit_breaker import NEWS_CRAWLER_CB

logger = setup_logger("news")


class NewsCrawler:
    """뉴스 크롤러 (Circuit Breaker 적용)"""
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def connect(self):
        """세션 연결"""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        logger.info("NewsCrawler connected")
    
    async def disconnect(self):
        """세션 종료 (추가)"""
        if self._session:
            await self._session.close()
            self._session = None
            logger.info("NewsCrawler disconnected")
    
    @NEWS_CRAWLER_CB.protect
    async def fetch_news(self, ticker: str, limit: int = 5) -> List[Dict]:
        if self._session is None:
            await self.connect()
        
        url = f"https://search.naver.com/search.naver?where=news&query={ticker}"
        
        try:
            async with self._session.get(url, timeout=10) as resp:
                html = await resp.text()
                headlines = re.findall(r'<a class="news_tit"[^>]*>([^<]+)</a>', html)
                summaries = re.findall(r'<div class="news_dsc"[^>]*>([^<]+)</div>', html)
                
                results = []
                for i in range(min(len(headlines), limit)):
                    results.append({
                        "title": headlines[i] if i < len(headlines) else "",
                        "summary": summaries[i] if i < len(summaries) else "",
                        "source": "naver",
                        "timestamp": datetime.now().isoformat()
                    })
                
                logger.debug(f"Fetched {len(results)} news for {ticker}")
                return results
                
        except Exception as e:
            logger.error(f"News fetch failed for {ticker}: {e}")
            return []