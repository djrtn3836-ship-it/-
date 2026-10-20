# -*- coding: utf-8 -*-
"""공시 본문 파싱·자사주 규모 연구(S143) 테스트."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scheduler.disclosure_body as db  # noqa: E402
from validation.disclosure_buyback_test import (  # noqa: E402
    build_rows,
    dedupe_rows,
    spearman,
    stats,
    summarize,
)

SAMPLE = (
    "주요사항보고서(자기주식 취득 결정) 1.취득예정주식(주) 보통주식 50,144,628 기타주식 6,912,036 "
    "2.취득예정금액(원) 보통주식 2,682,737,598,000 기타주식 317,262,452,400 "
    "3.취득예상기간 시작일 2024년 11월 18일 ③ 발행주식총수의 1% : 보통주 59,697,826주 "
    "자기주식 취득후 전량 소각할 계획임"
)


class TestParse:

    def test_parse_buyback(self):
        r = db.parse_buyback(SAMPLE)
        assert r["buy_shares"] == 50144628
        assert r["buy_amount"] == 2682737598000
        assert r["float_shares"] == 5969782600
        assert r["ratio_pct"] == 0.84
        assert r["cancel_flag"] is True

    def test_parse_without_note(self):
        r = db.parse_buyback("1.취득예정주식(주) 보통주식 10,000")
        assert r["buy_shares"] == 10000 and r["float_shares"] is None
        assert r["ratio_pct"] is None

    def test_get_buyback_cache(self, monkeypatch, tmp_path):
        calls = {"n": 0}

        def fake_fetch(rcept_no, api_key, **kw):
            calls["n"] += 1
            return SAMPLE

        monkeypatch.setattr(db, "fetch_text", fake_fetch)
        cache = tmp_path / "c.json"
        r1 = db.get_buyback("X1", api_key="k", cache_path=cache, sleep=0)
        r2 = db.get_buyback("X1", api_key="k", cache_path=cache, sleep=0)
        assert r1["buy_shares"] == 50144628 and r2["buy_shares"] == 50144628
        assert calls["n"] == 1, "두 번째는 캐시"


class TestStats:

    def test_spearman(self):
        assert spearman([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) == 1.0
        assert spearman([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) == -1.0

    def test_stats(self):
        s = stats([1.0, 3.0])
        assert s["n"] == 2 and s["mean"] == 2.0


def make_days(n=60, start="2022-01-03"):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


class TestBuildRows:

    def test_row_fields(self):
        days = make_days()
        closes = [100.0 + i for i in range(60)]
        vols = [1000.0] * 60
        ev = [{"date": "20220107", "title": "주요사항보고서(자기주식취득결정)", "rcept_no": "R1"}]
        body = lambda rn: {"ok": True, "ratio_pct": 1.5, "buy_amount": 50000.0, "cancel_flag": True}
        rows = build_rows("A", ev, days, closes, vols, days, [100.0] * 60, body_fn=body)
        assert len(rows) == 1
        r = rows[0]
        assert r["ratio_pct"] == 1.5 and r["cancel_flag"] is True
        assert r["amt_days"] == round(50000.0 / 102000.0, 2), "가용 5일 평균거래대금"
        assert r["fwd"]["1"] == round((105 / 104 - 1) - 0.0, 6)

    def test_dedupe(self):
        rows = [{"ticker": "A", "base": 5}, {"ticker": "A", "base": 15}, {"ticker": "A", "base": 30}]
        out = dedupe_rows(rows)
        assert [r["base"] for r in out] == [5, 30]


class TestSummarize:

    def test_splits(self):
        rows = [
            {"amt_days": 2.0, "ratio_pct": 2.0, "cancel_flag": True, "fwd": {"1": 0.02, "5": 0.03}},
            {"amt_days": 0.5, "ratio_pct": 0.5, "cancel_flag": False, "fwd": {"1": -0.01, "5": -0.01}},
        ]
        res = summarize(rows)
        assert res["amt"]["big"]["n"] == 1 and res["amt"]["small"]["n"] == 1
        assert res["ratio"]["big"]["mean"] == 2.0
        assert res["cancel"]["yes"]["n"] == 1
