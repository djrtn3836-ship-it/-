# -*- coding: utf-8 -*-
"""tests/unit/test_sentiment_pipeline.py - SentimentPipeline 단위 테스트 (22개)"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from data.news_sentiment import SentimentLabel, SentimentResult
from orchestrator.sentiment_pipeline import SentimentPipeline, _is_market_hours


def _make_result(ticker: str, score: float = 0.5) -> SentimentResult:
    return SentimentResult(ticker=ticker, score=score, label=SentimentLabel.from_score(score),
                            confidence=0.7, news_count=3, positive_count=2,
                            negative_count=1, keyword_hits=["어닝서프라이즈"])


@pytest.fixture
def mock_crawler():
    crawler = AsyncMock()
    crawler.get_news = AsyncMock(return_value=[{"title": "어닝서프라이즈", "content": "사상최대 실적"}])
    return crawler


@pytest.fixture
def mock_analyzer():
    analyzer = AsyncMock()
    analyzer.analyze = AsyncMock(return_value=_make_result("005930", 0.6))
    analyzer.get_cache_stats = MagicMock(return_value={"total_entries": 1})
    return analyzer


@pytest.fixture
def pipeline(mock_crawler, mock_analyzer):
    return SentimentPipeline(news_crawler=mock_crawler, analyzer=mock_analyzer, max_news_per_ticker=10)


@pytest.fixture
def pipeline_no_crawler():
    return SentimentPipeline(news_crawler=None)


class TestIsMarketHours:
    def test_returns_bool(self):
        assert isinstance(_is_market_hours(), bool)


class TestSentimentPipelineInit:
    def test_init_with_crawler(self, mock_crawler):
        assert SentimentPipeline(news_crawler=mock_crawler)._crawler is mock_crawler

    def test_init_without_crawler(self):
        assert SentimentPipeline(news_crawler=None)._crawler is None

    def test_not_running_initially(self, pipeline):
        assert pipeline._running is False

    def test_active_tickers_empty_initially(self, pipeline):
        assert pipeline._active_tickers == []

    def test_set_active_tickers(self, pipeline):
        pipeline.set_active_tickers(["005930", "000660"])
        assert "005930" in pipeline._active_tickers


class TestEnrich:
    def test_adds_sentiment_score(self, pipeline):
        result = asyncio.run(pipeline.enrich({"ticker": "005930", "price": 70000}))
        assert 0.0 <= result["sentiment_score"] <= 1.0

    def test_no_crawler_returns_neutral(self, pipeline_no_crawler):
        result = asyncio.run(pipeline_no_crawler.enrich({"ticker": "005930", "price": 70000}))
        assert result["sentiment_score"] == 0.5

    def test_empty_ticker_returns_neutral(self, pipeline):
        result = asyncio.run(pipeline.enrich({"ticker": "", "price": 70000}))
        assert result["sentiment_score"] == 0.5

    def test_preserves_original_data(self, pipeline):
        result = asyncio.run(pipeline.enrich({"ticker": "005930", "price": 70000, "volume": 100}))
        assert result["price"] == 70000 and result["volume"] == 100

    def test_does_not_mutate_original(self, pipeline):
        data = {"ticker": "005930", "price": 70000}
        keys_before = set(data.keys())
        asyncio.run(pipeline.enrich(data))
        assert set(data.keys()) == keys_before

    def test_exception_returns_neutral(self, mock_analyzer):
        mock_analyzer.analyze = AsyncMock(side_effect=Exception("DB error"))
        p = SentimentPipeline(news_crawler=AsyncMock(), analyzer=mock_analyzer)
        result = asyncio.run(p.enrich({"ticker": "005930", "price": 70000}))
        assert result["sentiment_score"] == 0.5


class TestGetSentiment:
    def test_returns_result(self, pipeline):
        result = asyncio.run(pipeline.get_sentiment("005930"))
        assert result is not None and result.ticker == "005930"

    def test_returns_none_on_exception(self, mock_analyzer):
        mock_analyzer.analyze = AsyncMock(side_effect=Exception("error"))
        p = SentimentPipeline(news_crawler=AsyncMock(), analyzer=mock_analyzer)
        assert asyncio.run(p.get_sentiment("005930")) is None

    def test_no_crawler_neutral_or_none(self, pipeline_no_crawler):
        result = asyncio.run(pipeline_no_crawler.get_sentiment("005930"))
        if result is not None:
            assert result.label == SentimentLabel.NEUTRAL


class TestStartStop:
    def test_start_sets_running(self, pipeline):
        async def _run():
            await pipeline.start()
            assert pipeline._running is True
            await pipeline.stop()
        asyncio.run(_run())

    def test_stop_clears_running(self, pipeline):
        async def _run():
            await pipeline.start()
            await pipeline.stop()
            assert pipeline._running is False
        asyncio.run(_run())

    def test_double_start_idempotent(self, pipeline):
        async def _run():
            await pipeline.start()
            task1 = pipeline._refresh_task
            await pipeline.start()
            assert pipeline._refresh_task is task1
            await pipeline.stop()
        asyncio.run(_run())


class TestGetStatus:
    def test_status_structure(self, pipeline):
        status = pipeline.get_status()
        required = {"running", "active_tickers", "cached_tickers", "fresh_cache",
                    "crawler_available", "analyzer_cache"}
        assert required.issubset(status.keys())

    def test_crawler_available_true(self, pipeline):
        assert pipeline.get_status()["crawler_available"] is True

    def test_crawler_available_false(self, pipeline_no_crawler):
        assert pipeline_no_crawler.get_status()["crawler_available"] is False

    def test_active_tickers_count(self, pipeline):
        pipeline.set_active_tickers(["A", "B", "C"])
        assert pipeline.get_status()["active_tickers"] == 3
