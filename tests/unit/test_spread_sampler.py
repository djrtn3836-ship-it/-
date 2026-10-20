# -*- coding: utf-8 -*-
import json

from scheduler import spread_sampler as ss


class FakeMonitor:
    def __init__(self):
        self.books = {
            "005930": {"bids": [(70000.0, 10)], "asks": [(70100.0, 5)]},
            "000660": {"bids": [(120000.0, 3)], "asks": [(120100.0, 2)]},
            "999999": {"bids": [], "asks": []},
        }

    def get_subscribed_tickers(self):
        return list(self.books)

    def get_orderbook(self, t):
        return self.books.get(t)


async def test_sample_spreads(tmp_path):
    out = tmp_path / "s.jsonl"
    r = await ss.sample_spreads(FakeMonitor(), out=out, ts="2026-10-20T10:05:00")
    assert r["sampled"] == 2
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["ticker"] == "005930"
    assert abs(rows[0]["spread_bp"] - 100.0 / 70050.0 * 10000.0) < 0.1
    assert r["median_bp"] is not None


async def test_sample_skips_without_monitor(tmp_path):
    r = await ss.sample_spreads(None, out=tmp_path / "s.jsonl")
    assert r["sampled"] == 0 and r.get("skipped")


def test_summarize(tmp_path):
    p = tmp_path / "s.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for i in range(25):
            f.write(json.dumps({"ts": "t", "ticker": "A", "price": 10000, "bid": 9990, "ask": 10010, "spread_bp": 20.0}) + "\n")
    s = ss.summarize(p)
    assert s["n"] == 25 and s["median_bp"] == 20.0
    assert s["buckets"]["5천~2만"]["n"] == 25
