# -*- coding: utf-8 -*-
"""공시 이벤트 연구·백필(S140) 테스트."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scheduler.disclosure_backfill import _dedupe, fetch_period  # noqa: E402
from validation.disclosure_event_study import (  # noqa: E402
    _grade,
    build_events,
    classify_title,
    dedupe_events,
    has_artifact,
    parse_date,
)


def make_days(n=60, start="2022-01-03"):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


class TestClassify:

    def test_core(self):
        assert classify_title("주요사항보고서(자사주취득결정)") == "자사주매입"
        assert classify_title("주요사항보고서(자사주 처분 결정)") == "자사주처분"
        assert classify_title("주요사항보고서(유상증자결정)") == "유상증자"
        assert classify_title("무상증자 결정") == "무상증자"
        assert classify_title("주식분할 결정") == "주식분할"
        assert classify_title("주요사항보고서(감자결정)") == "감자"
        assert classify_title("최대주주 변경") == "최대주주변경"
        assert classify_title("단일판매·공급계약 체결") == "수주계약"
        assert classify_title("잠정실적 공시") == "실적공시"
        assert classify_title("불성실공시법인 지정") == "불성실공시"
        assert classify_title("전환사채 발행 결정") == "CB/BW"
        assert classify_title("현금배당 결정") == "배당"
        assert classify_title("횡령·배임 혐의 발생") == "횡령배임"
        assert classify_title("소송 등의 제기") == "소송"

    def test_routine_is_none(self):
        assert classify_title("임원ㆍ주요주주특정증권등소유상황보고서") is None
        assert classify_title("기타시장안내(연구개발 우수기업)") is None
        assert classify_title("") is None

    def test_parse_date(self):
        assert parse_date("20220105") == datetime(2022, 1, 5)
        assert parse_date("2022-01-05") == datetime(2022, 1, 5)
        assert parse_date("bad") is None


class TestBuildEvents:

    def test_excess_return_alignment(self):
        days = make_days()
        closes = [100.0 + i for i in range(60)]
        k_days = list(days)
        k_closes = [100.0] * 60
        ev = build_events(
            "000001",
            [{"date": "20220105", "title": "자사주취득결정"}],
            days, closes, k_days, k_closes,
        )
        assert len(ev) == 1
        row = ev[0]
        assert row["base"] == 2, "01-05가 3번째 거래일"
        assert abs(row["fwd"]["1"] - (103.0 / 102.0 - 1.0)) < 1e-9
        assert abs(row["fwd"]["5"] - (107.0 / 102.0 - 1.0)) < 1e-9
        assert abs(row["fwd"]["20"] - (122.0 / 102.0 - 1.0)) < 1e-9

    def test_kospi_subtracted(self):
        days = make_days()
        closes = [100.0 + i for i in range(60)]
        k_closes = [100.0 + 0.5 * i for i in range(60)]
        ev = build_events(
            "000001", [{"date": "20220105", "title": "유상증자결정"}],
            days, closes, days, k_closes,
        )
        assert abs(ev[0]["fwd"]["5"] - ((107 / 102 - 1) - (103.5 / 101 - 1))) < 1e-9

    def test_artifact_skips_horizon(self):
        days = make_days()
        closes = [100.0 + i for i in range(60)]
        closes[10] = 160.0
        assert has_artifact(closes, 2, 22) is True
        assert has_artifact(closes, 2, 7) is False
        ev = build_events(
            "000001", [{"date": "20220103", "title": "자사주취득"}],
            days, closes, days, [100.0] * 60,
        )
        assert "20" not in ev[0]["fwd"] and "1" in ev[0]["fwd"] and "5" in ev[0]["fwd"]

    def test_unknown_title_skipped(self):
        days = make_days()
        ev = build_events(
            "000001", [{"date": "20220105", "title": "임원 소유상황보고서"}],
            days, [100.0] * 60, days, [100.0] * 60,
        )
        assert ev == []


class TestDedupe:

    def test_overlap_dropped(self):
        events = [
            {"ticker": "A", "category": "배당", "base": 5},
            {"ticker": "A", "category": "배당", "base": 15},
            {"ticker": "A", "category": "배당", "base": 30},
            {"ticker": "B", "category": "배당", "base": 6},
        ]
        out = dedupe_events(events)
        bases = [(e["ticker"], e["base"]) for e in out]
        assert ("A", 5) in bases and ("A", 15) not in bases and ("A", 30) in bases
        assert ("B", 6) in bases, "다른 종목은 독립"


class TestVerdict:

    def _st(self, n=40, t=2.5, pos=4, sig=4, freq=8.0, mean=2.0, median=1.5):
        return {
            "freq_per_year": freq,
            "horizons": {"5": {"n": n, "t": t, "pos_years": pos, "sig_years": sig,
                               "mean": mean, "median": median}},
        }

    def test_grades(self):
        assert _grade(self._st()) == "A_생존"
        assert _grade(self._st(t=1.7)) == "B_경계"
        assert _grade(self._st(t=1.2)) == "C_참고"
        assert _grade(self._st(n=20, t=3.0)) == "표본부족"
        assert _grade(self._st(t=2.5, pos=1, sig=5)) == "C_참고", "연도 일관성 실패"

    def test_mean_median_mismatch_downgrades(self):
        assert _grade(self._st(mean=3.0, median=-0.5)) == "C_참고", "극단값이 끄는 평균"
        assert _grade(self._st(mean=-3.0, median=0.5)) == "C_참고"


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Session:
    def __init__(self, pages):
        self.pages = pages
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        page = int((params or {}).get("page_no", 1))
        self.calls += 1
        return _Resp(self.pages[page - 1])


class TestBackfillFetch:

    def test_pagination(self):
        pages = [
            {"status": "000", "total_page": 2, "list": [
                {"rcept_dt": "20220103", "report_nm": f"t{i}", "pblntf_ty": "B"} for i in range(100)
            ]},
            {"status": "000", "total_page": 2, "list": [
                {"rcept_dt": "20220201", "report_nm": "t100", "pblntf_ty": "B"}
            ]},
        ]
        sess = _Session(pages)
        rows = fetch_period("00126380", "20220101", "20221231", "key", session=sess, sleep=0)
        assert len(rows) == 101 and sess.calls == 2

    def test_no_data(self):
        rows = fetch_period("00126380", "20220101", "20221231", "key",
                            session=_Session([{"status": "013"}]), sleep=0)
        assert rows == []

    def test_dedupe(self):
        events = [
            {"date": "20220103", "title": "A"},
            {"date": "20220103", "title": "A"},
            {"date": "20220104", "title": "B"},
            {"date": "", "title": "C"},
        ]
        assert len(_dedupe(events)) == 2
