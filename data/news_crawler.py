"""
data/news_crawler.py - v6.2.0 (NAVER API HUB 완전 대응)
- NAVER API HUB 전용 URL 및 헤더 사용
- format=json & Accept: application/json 명시
- re 모듈 임포트 추가 (HTML 태그 제거)
- 기존 캐싱 및 감성 분석 기능 완전 유지
"""

import os
import re
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
    """뉴스 크롤러 (NAVER API HUB)"""

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

        # 종목명 매핑
        stock_name_map = {
            "005930": "삼성전자",
            "000660": "SK하이닉스",
            "005380": "현대차",
            "035420": "NAVER",
            "051910": "LG화학",
            "006400": "삼성SDI",
            "207940": "삼성바이오로직스",
        }
        query = stock_name_map.get(ticker, ticker)

        # 🔥 NAVER API HUB 전용 URL
        url = "https://naverapihub.apigw.ntruss.com/search/v1/news"

        # 🔥 NAVER API HUB 전용 헤더 (Accept: application/json 포함)
        headers = {
            "X-NCP-APIGW-API-KEY-ID": self.client_id,
            "X-NCP-APIGW-API-KEY": self.client_secret,
            "Accept": "application/json",  # 🔥 핵심: JSON 응답 요청
        }

        # 🔥 format=json 필수 파라미터
        params = {
            "query": query,
            "display": limit,
            "sort": "date",
            "format": "json",  # 🔥 핵심
        }

        try:
            async with self._session.get(url, headers=headers, params=params, timeout=10) as resp:
                if resp.status == 200:
                    # JSON 응답 처리 (text/plain으로 올 경우도 대비)
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

                    results = []
                    texts_for_sentiment = []
                    for item in items:
                        # HTML 태그 제거 (re 모듈 사용)
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

                    # 감성 분석 수행
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

    # ============================================================
    # 🔥 종목별 캐시 (성능 최적화) - 기존 기능 완전 유지
    # ============================================================
    _cache = {}
    _cache_time = {}

    async def get_news_with_sentiment(self, ticker: str, limit: int = 5, cache_seconds: int = 3600) -> Tuple[List[Dict], float]:
        """캐시 적용된 뉴스 + 감성 점수 조회"""
        cache_key = f"{ticker}_{limit}"
        now = datetime.now().timestamp()

        if cache_key in self._cache and (now - self._cache_time.get(cache_key, 0)) < cache_seconds:
            logger.debug(f"📦 캐시 히트: {ticker}")
            return self._cache[cache_key]

        news, sentiment = await self.fetch_news(ticker, limit)
        self._cache[cache_key] = (news, sentiment)
        self._cache_time[cache_key] = now
        return news, sentiment