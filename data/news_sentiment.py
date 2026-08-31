# -*- coding: utf-8 -*-
"""
data/news_sentiment.py - 뉴스 감성 분석기 v1.0

설계 원칙:
    - 순수 Python (VADER/KoNLP 미사용 — 프로젝트의 경량 의존성 철학 준수)
    - 한국어 금융 키워드 사전 + 영문 경량 키워드 사전 앙상블
    - 비동기 안전: asyncio.Lock 보호, TTL 30분 캐시
    - 감성 점수: -1.0 (매우 부정) ~ +1.0 (매우 긍정)

주의:
    이름에 "키워드 기반"이라 명시하듯, 실제 VADER 알고리즘(부정어/강조어 처리)이나
    KoNLP 형태소 분석은 사용하지 않습니다. 로드맵 표기("VADER+KoNLP 앙상블")와의
    오해를 막기 위해 이 사실을 명확히 문서화합니다.
"""

import asyncio
import hashlib
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from core.logger import setup_logger
from observability.tracer import get_tracer

logger = setup_logger("news_sentiment")
trace = get_tracer(__name__)


class SentimentLabel(Enum):
    VERY_POSITIVE = "VERY_POSITIVE"
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    VERY_NEGATIVE = "VERY_NEGATIVE"

    @classmethod
    def from_score(cls, score: float) -> "SentimentLabel":
        if score >= 0.5:
            return cls.VERY_POSITIVE
        elif score >= 0.2:
            return cls.POSITIVE
        elif score >= -0.2:
            return cls.NEUTRAL
        elif score >= -0.5:
            return cls.NEGATIVE
        return cls.VERY_NEGATIVE


# ─── 한국어 금융 키워드 사전 (긍정 25 / 부정 25) ──────────────────────
_KR_POSITIVE_KEYWORDS: List[Tuple[str, float]] = [
    ("어닝서프라이즈", 0.9), ("깜짝실적", 0.85), ("사상최대", 0.85),
    ("실적개선", 0.75), ("흑자전환", 0.80), ("매출증가", 0.70),
    ("영업이익", 0.60), ("순이익증가", 0.70), ("목표주가상향", 0.80),
    ("신고가", 0.75), ("52주신고가", 0.80), ("연고점", 0.70),
    ("외국인매수", 0.65), ("기관매수", 0.65), ("순매수", 0.60),
    ("대규모수주", 0.80), ("계약체결", 0.70), ("수주잔고", 0.65),
    ("신사업", 0.55), ("글로벌진출", 0.60), ("특허등록", 0.55),
    ("FDA승인", 0.85), ("임상성공", 0.80), ("기술수출", 0.75),
    ("지분취득", 0.55),
]

_KR_NEGATIVE_KEYWORDS: List[Tuple[str, float]] = [
    ("어닝쇼크", -0.90), ("실적부진", -0.75), ("적자전환", -0.80),
    ("영업손실", -0.75), ("매출감소", -0.70), ("목표주가하향", -0.75),
    ("신저가", -0.75), ("52주신저가", -0.80), ("연저점", -0.70),
    ("외국인매도", -0.65), ("기관매도", -0.65), ("순매도", -0.60),
    ("대량매도", -0.70), ("블록딜", -0.55), ("검찰수사", -0.90),
    ("횡령", -0.95), ("배임", -0.90), ("과징금", -0.75),
    ("영업정지", -0.85), ("상장폐지", -0.95), ("감사의견거절", -0.90),
    ("불성실공시", -0.80), ("임상실패", -0.85), ("FDA거부", -0.85),
    ("리콜", -0.75),
]

# 영문 경량 키워드 (긍정 15 / 부정 15)
_EN_POSITIVE_KEYWORDS: List[Tuple[str, float]] = [
    ("beat", 0.7), ("exceed", 0.7), ("record", 0.6), ("growth", 0.6),
    ("profit", 0.55), ("upgrade", 0.7), ("buy", 0.5), ("strong", 0.55),
    ("surge", 0.7), ("rally", 0.65), ("gain", 0.6), ("outperform", 0.75),
    ("approved", 0.75), ("contract", 0.55), ("dividend", 0.55),
]

_EN_NEGATIVE_KEYWORDS: List[Tuple[str, float]] = [
    ("miss", -0.7), ("loss", -0.65), ("decline", -0.6), ("cut", -0.55),
    ("downgrade", -0.7), ("sell", -0.5), ("weak", -0.55), ("drop", -0.65),
    ("fall", -0.6), ("underperform", -0.75), ("fraud", -0.9),
    ("investigation", -0.7), ("lawsuit", -0.6), ("recall", -0.7),
    ("bankruptcy", -0.9),
]

_CACHE_TTL_SECONDS = 1800  # 30분
_KNOWN_LIMITATION = (
    "일부 키워드가 다른 키워드의 부분 문자열(예: '신저가' ⊂ '52주신저가')이라 "
    "동일 사건이 중복 카운트될 수 있음 — 토큰 기반 매칭으로 개선 예정 (미해결 한계)"
)


@dataclass(frozen=True)
class NewsItem:
    """단일 뉴스 아이템."""
    title: str
    content: str = ""
    source: str = "unknown"
    published_at: float = field(default_factory=time.time)
    url: str = ""

    @property
    def full_text(self) -> str:
        return f"{self.title} {self.title} {self.content}".strip()


@dataclass
class SentimentResult:
    """감성 분석 결과 DTO."""
    ticker: str
    score: float
    label: SentimentLabel
    confidence: float
    news_count: int
    positive_count: int
    negative_count: int
    keyword_hits: List[str]
    analyzed_at: float = field(default_factory=time.time)

    @property
    def impact_score(self) -> float:
        """SignalPipeline 반영용 정규화 점수 (0~1).

        수식: impact_score = ((score + 1.0) / 2.0) * confidence
        """
        normalized = (self.score + 1.0) / 2.0
        return round(normalized * self.confidence, 4)

    def to_dict(self) -> Dict:
        return {
            "ticker": self.ticker, "score": round(self.score, 4),
            "label": self.label.value, "confidence": round(self.confidence, 4),
            "news_count": self.news_count, "positive_count": self.positive_count,
            "negative_count": self.negative_count, "keyword_hits": self.keyword_hits,
            "analyzed_at": self.analyzed_at, "impact_score": self.impact_score,
        }


class KeywordScorer:
    """키워드 빈도 기반 감성 스코어러."""

    def __init__(self) -> None:
        self._pos_dict: Dict[str, float] = {
            kw.lower(): w for kw, w in _KR_POSITIVE_KEYWORDS + _EN_POSITIVE_KEYWORDS
        }
        self._neg_dict: Dict[str, float] = {
            kw.lower(): w for kw, w in _KR_NEGATIVE_KEYWORDS + _EN_NEGATIVE_KEYWORDS
        }

    def score(self, text: str) -> Tuple[float, List[str]]:
        """텍스트 감성 점수 계산.

        raw_score = (pos_score - neg_score) / (pos_score + neg_score)
        smoothed  = tanh(raw_score * 2.0)   # 극단값 완화
        """
        text_lower = text.lower()
        pos_score, neg_score = 0.0, 0.0
        hits: List[Tuple[str, float]] = []

        for kw, w in self._pos_dict.items():
            if kw in text_lower:
                count = min(text_lower.count(kw), 3)
                pos_score += w * count
                hits.append((kw, w * count))

        for kw, w in self._neg_dict.items():
            if kw in text_lower:
                count = min(text_lower.count(kw), 3)
                neg_score += abs(w) * count
                hits.append((kw, w * count))

        total = pos_score + neg_score
        if total == 0:
            return 0.0, []

        raw_score = (pos_score - neg_score) / total
        smoothed = math.tanh(raw_score * 2.0)
        top_keywords = [kw for kw, _ in sorted(hits, key=lambda x: abs(x[1]), reverse=True)[:10]]
        return round(smoothed, 4), top_keywords


class TitleWeighter:
    """제목/본문 가중치 차등 적용기 (제목 2배)."""

    def __init__(self, title_weight: float = 2.0, content_weight: float = 1.0) -> None:
        self._title_w = title_weight
        self._content_w = content_weight

    def weighted_score(self, scorer: KeywordScorer, title: str, content: str) -> Tuple[float, List[str]]:
        title_score, title_kws = scorer.score(title)
        content_score, content_kws = scorer.score(content) if content else (0.0, [])

        total_weight = self._title_w + (self._content_w if content else 0.0)
        weighted = (
            title_score * self._title_w
            + content_score * (self._content_w if content else 0.0)
        ) / total_weight

        all_kws = list(dict.fromkeys(title_kws + content_kws))[:10]
        return round(weighted, 4), all_kws


class NewsSentimentAnalyzer:
    """뉴스 감성 분석기 (키워드 앙상블 + TTL 캐시)."""

    def __init__(self, cache_ttl: int = _CACHE_TTL_SECONDS, min_news_for_high_confidence: int = 5) -> None:
        self._scorer = KeywordScorer()
        self._weighter = TitleWeighter()
        self._cache: Dict[str, Tuple[SentimentResult, float]] = {}
        self._cache_ttl = cache_ttl
        self._min_news = min_news_for_high_confidence
        self._lock = asyncio.Lock()

    @trace.traced
    async def analyze(self, ticker: str, news_list: List[NewsItem], force_refresh: bool = False) -> SentimentResult:
        cache_key = self._make_cache_key(ticker, news_list)

        if not force_refresh:
            cached = self._get_from_cache(cache_key)
            if cached is not None:
                return cached

        async with self._lock:
            if not force_refresh:
                cached = self._get_from_cache(cache_key)
                if cached is not None:
                    return cached
            result = self._compute(ticker, news_list)
            self._cache[cache_key] = (result, time.time())
            return result

    def _compute(self, ticker: str, news_list: List[NewsItem]) -> SentimentResult:
        if not news_list:
            return SentimentResult(
                ticker=ticker, score=0.0, label=SentimentLabel.NEUTRAL,
                confidence=0.0, news_count=0, positive_count=0,
                negative_count=0, keyword_hits=[],
            )

        scores: List[float] = []
        all_keywords: List[str] = []
        positive_count = negative_count = 0

        for item in news_list:
            s, kws = self._weighter.weighted_score(self._scorer, item.title, item.content)
            scores.append(s)
            all_keywords.extend(kws)
            if s > 0.1:
                positive_count += 1
            elif s < -0.1:
                negative_count += 1

        avg_score = sum(scores) / len(scores)

        news_conf = min(len(news_list) / max(self._min_news, 1), 1.0)
        if len(scores) > 1:
            variance = sum((s - avg_score) ** 2 for s in scores) / len(scores)
            consistency = max(0.0, 1.0 - math.sqrt(variance))
        else:
            consistency = 0.5
        # confidence = 0.6 * news_conf + 0.4 * consistency
        confidence = round(news_conf * 0.6 + consistency * 0.4, 4)

        top_kws = list(dict.fromkeys(all_keywords))[:5]

        return SentimentResult(
            ticker=ticker, score=round(avg_score, 4),
            label=SentimentLabel.from_score(avg_score), confidence=confidence,
            news_count=len(news_list), positive_count=positive_count,
            negative_count=negative_count, keyword_hits=top_kws,
        )

    def _make_cache_key(self, ticker: str, news_list: List[NewsItem]) -> str:
        titles = "".join(n.title for n in news_list[:10])
        h = hashlib.md5(f"{ticker}:{titles}".encode()).hexdigest()[:8]
        return f"{ticker}:{h}"

    def _get_from_cache(self, key: str) -> Optional[SentimentResult]:
        if key not in self._cache:
            return None
        result, ts = self._cache[key]
        if time.time() - ts > self._cache_ttl:
            del self._cache[key]
            return None
        return result

    def clear_cache(self, ticker: Optional[str] = None) -> None:
        if ticker:
            for k in [k for k in self._cache if k.startswith(ticker)]:
                del self._cache[k]
        else:
            self._cache.clear()

    def get_cache_stats(self) -> Dict:
        now = time.time()
        valid = sum(1 for _, ts in self._cache.values() if now - ts <= self._cache_ttl)
        return {
            "total_entries": len(self._cache), "valid_entries": valid,
            "expired_entries": len(self._cache) - valid, "ttl_seconds": self._cache_ttl,
        }


_analyzer_instance: Optional[NewsSentimentAnalyzer] = None


def get_sentiment_analyzer() -> NewsSentimentAnalyzer:
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = NewsSentimentAnalyzer()
    return _analyzer_instance
