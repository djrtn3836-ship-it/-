# -*- coding: utf-8 -*-
"""감성 이력 축적(S148) 테스트."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scheduler import sentiment_history as sh  # noqa: E402


class FakeCrawler:
    def __init__(self, news):
        self.news = news
        self.calls = 0

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def get_news_with_sentiment(self, ticker, limit=5):
        self.calls += 1
        return self.news.get(ticker, []), 0.0


class FakeAnalyzer:
    async def analyze(self, ticker, items):
        return SimpleNamespace(
            score=0.7, label=SimpleNamespace(value="POSITIVE"), confidence=0.8,
            news_count=len(items), positive_count=len(items), negative_count=0,
        )


@pytest.fixture(autouse=True)
def _names(monkeypatch):
    monkeypatch.setattr(
        "infrastructure.market_data.universe_provider.get_universe",
        lambda: {"005930": "삼성전자", "000660": "SK하이닉스"},
    )


NEWS = {
    "005930": [{"title": "삼성전자 호실적", "summary": "영업이익 증가", "pub_date": "Tue, 20 Oct 2026 09:00:00 +0900"}],
    "000660": [],
}


class TestCollect:

    def test_records_and_dedupes(self, tmp_path):
        log = tmp_path / "s.jsonl"
        crawler = FakeCrawler(NEWS)
        out = __import__("asyncio").run(sh.collect_daily(
            ["005930", "000660"], log_path=log, crawler=crawler, analyzer=FakeAnalyzer(),
            sleep=0, today="2026-10-20",
        ))
        assert out["collected"] == 1 and out["skipped"] == 1, "뉴스 없으면 기록 생략"
        rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
        assert rows[0]["ticker"] == "005930" and rows[0]["name"] == "삼성전자"
        assert rows[0]["score"] == 0.7 and rows[0]["label"] == "POSITIVE"

        again = __import__("asyncio").run(sh.collect_daily(
            ["005930"], log_path=log, crawler=crawler, analyzer=FakeAnalyzer(),
            sleep=0, today="2026-10-20",
        ))
        assert again["collected"] == 0, "같은 날 중복 기록 금지"

    def test_no_news_no_row(self, tmp_path):
        log = tmp_path / "s.jsonl"
        out = __import__("asyncio").run(sh.collect_daily(
            ["000660"], log_path=log, crawler=FakeCrawler(NEWS) if False else FakeCrawler(NEWS),
            analyzer=FakeAnalyzer(), sleep=0, today="2026-10-20",
        ))
        assert out["collected"] == 0 and out["skipped"] == 1
        assert not log.exists() or log.read_text(encoding="utf-8").strip() == ""

    def test_summary(self, tmp_path):
        log = tmp_path / "s.jsonl"
        log.write_text(
            json.dumps({"date": "2026-10-20", "ticker": "005930", "score": 0.7}, ensure_ascii=False) + "\n"
            + json.dumps({"date": "2026-10-20", "ticker": "000660", "score": 0.3}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        s = sh.summary(log)
        assert s["days"] == 1 and s["tickers"] == 2 and s["rows"] == 2
        assert sh.summary(tmp_path / "none.jsonl") == {}


class TestWiring:

    def test_bootstrap_job(self):
        src = (Path(__file__).resolve().parents[2] / "app" / "bootstrap.py").read_text(encoding="utf-8")
        assert "scheduled_sentiment_history" in src and "sentiment_history" in src
        assert 'job_count"] = 31' in src
