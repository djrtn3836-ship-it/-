# -*- coding: utf-8 -*-
"""공시 일일 감시(S141) 테스트 — 수집·필터·기록·중복·알림·표시."""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scheduler.disclosure_watch as dw  # noqa: E402
from report import html_dashboard as hd  # noqa: E402
from report.briefing import _disclosure_section  # noqa: E402


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

    def close(self):
        pass


STUDY = {
    "categories": {
        "무상증자": {"grade": "A_생존", "horizons": {"5": {"n": 48, "mean": 4.94, "hit": 60.4}}},
        "유상증자": {"grade": "B_경계", "horizons": {"5": {"n": 490, "mean": -0.12, "hit": 42.9}}},
        "배당": {"grade": "C_참고", "horizons": {"5": {"n": 1501, "mean": -0.1, "hit": 46.0}}},
    }
}


def _day_pages():
    return [{
        "status": "000", "total_page": 2, "list": [
            {"rcept_dt": "20261019", "stock_code": "005930", "report_nm": "주요사항보고서(무상증자결정)", "pblntf_ty": "B"},
            {"rcept_dt": "20261019", "stock_code": "000660", "report_nm": "주요사항보고서(유상증자결정)", "pblntf_ty": "B"},
            {"rcept_dt": "20261019", "stock_code": "", "report_nm": "증권발행실적보고서(집합투자증권)", "pblntf_ty": "C"},
        ],
    }, {
        "status": "000", "total_page": 2, "list": [
            {"rcept_dt": "20261019", "stock_code": "005930", "report_nm": "임원ㆍ주주주식소유상황보고서", "pblntf_ty": "D"},
        ],
    }]


class TestFetch:

    def test_filters_unlisted_and_paginates(self):
        rows = dw.fetch_day("20261019", "key", session=_Session(_day_pages()), sleep=0)
        assert len(rows) == 3, "펀드(코드 없음) 제외"
        assert {r["ticker"] for r in rows} == {"005930", "000660"}

    def test_scan_dates_weekday_only(self):
        dates = dw._scan_dates(days=5, today=datetime(2026, 10, 19))  # 월
        assert dates == ["20261015", "20261016", "20261019"]


class TestRunWatch:

    def _setup(self, tmp_path, monkeypatch):
        study = tmp_path / "study.json"
        study.write_text(json.dumps(STUDY, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(
            "infrastructure.market_data.universe_provider.get_universe",
            lambda: {"005930": "삼성전자", "000660": "SK하이닉스"},
        )
        return study

    def test_records_and_dedupes(self, tmp_path, monkeypatch):
        study = self._setup(tmp_path, monkeypatch)
        log = tmp_path / "watch.jsonl"
        state = tmp_path / "state.json"

        def run():
            return dw.run_watch(
                1, api_key="k", session=_Session(_day_pages()), study_path=study,
                log_path=log, state_path=state, send_alert=False,
            )

        first = run()
        assert first["new"] == 2 and first["a_grade"] == 1, "무상증자만 A"
        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        rec = json.loads(lines[0])
        assert rec["name"] == "삼성전자" and rec["grade"] == "A_생존"

        second = run()
        assert second["new"] == 0, "같은 공시 재기록 금지"
        assert len(log.read_text(encoding="utf-8").splitlines()) == 2
        assert json.loads(state.read_text(encoding="utf-8"))["last_new"] == 0

    def test_skips_ungraded(self, tmp_path, monkeypatch):
        study = tmp_path / "study.json"
        study.write_text(json.dumps({"categories": {}}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(
            "infrastructure.market_data.universe_provider.get_universe",
            lambda: {"005930": "삼성전자"},
        )
        out = dw.run_watch(
            1, api_key="k", session=_Session(_day_pages()), study_path=study,
            log_path=tmp_path / "l.jsonl", state_path=tmp_path / "s.json", send_alert=False,
        )
        assert out["new"] == 0


class TestEventsForDay:

    def _log(self, tmp_path):
        rows = [
            {"date": "20261019", "ticker": "A", "category": "무상증자", "grade": "A_생존", "name": "가"},
            {"date": "20261019", "ticker": "A", "category": "무상증자", "grade": "A_생존", "name": "가"},
            {"date": "20261019", "ticker": "B", "category": "배당", "grade": "C_참고", "name": "나"},
            {"date": "20261018", "ticker": "C", "category": "유상증자", "grade": "B_경계", "name": "다"},
        ]
        p = tmp_path / "l.jsonl"
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        return p

    def test_filters_and_dedupes(self, tmp_path):
        p = self._log(tmp_path)
        rows = dw.events_for_day("2026-10-19", log_path=p)
        assert len(rows) == 1 and rows[0]["ticker"] == "A", "C급 제외 + 중복 제거"
        rows2 = dw.events_for_day("20261018", log_path=p)
        assert len(rows2) == 1

    def test_latest_day(self, tmp_path):
        p = self._log(tmp_path)
        assert dw.latest_event_day(log_path=p) == "20261019"


class TestDisplay:

    def test_format_alert(self):
        text = dw.format_alert([{"category": "무상증자", "name": "삼성전자", "ticker": "005930",
                                 "mean5": 4.94, "hit5": 60.4, "n5": 48}])
        assert "무상증자" in text and "삼성전자" in text and "+4.94%" in text and "역사 1일" in text

    def test_format_alert_buyback_body(self):
        ev = [{"category": "자사주매입", "name": "삼성전자", "ticker": "005930",
               "mean5": 0.77, "hit5": 52.4, "n5": 571,
               "buy_amount": 2.68e12, "ratio_pct": 0.84, "cancel_flag": True}]
        text = dw.format_alert(ev, entry_stats={})
        assert "취득금액 2.68조" in text and "비율 0.84%" in text and "소각" in text

    def test_fetch_day_has_rcept_no(self):
        rows = dw.fetch_day("20261019", "key", session=_Session(_day_pages()), sleep=0)
        assert all("rcept_no" in r for r in rows)

    def test_format_alert_entry_stats(self):
        ev = [{"category": "무상증자", "name": "삼성전자", "ticker": "005930",
               "mean1": 2.32, "hit1": 62.8, "n1": 43, "mean5": 4.94, "hit5": 60.4, "n5": 48}]
        text = dw.format_alert(ev, entry_stats={"무상증자": {"e2_mean": 3.71, "e2_n": 49}})
        assert "다음날 시가 진입 5일 +3.71%" in text
        ev2 = [{"category": "합병", "name": "A", "ticker": "000001", "mean5": 1.13, "hit5": 52.7, "n5": 150}]
        text2 = dw.format_alert(ev2, entry_stats={"합병": {"e2_mean": 0.58, "e2_n": 150}})
        assert "당일 소멸" in text2

    def test_load_entry_stats(self, tmp_path):
        p = tmp_path / "entry.json"
        p.write_text(json.dumps({"무상증자": {"entries": {"E2_5": {"mean": 3.71, "n": 49}}}}), encoding="utf-8")
        st = dw.load_entry_stats(p)
        assert st["무상증자"]["e2_mean"] == 3.71 and st["무상증자"]["e2_n"] == 49

    def test_briefing_section(self, monkeypatch):
        monkeypatch.setattr(dw, "events_for_day", lambda *a, **kw: [{
            "ticker": "005930", "name": "삼성전자", "category": "무상증자",
            "grade": "A_생존", "mean5": 4.94, "hit5": 60.4,
        }])
        lines = _disclosure_section("2026-10-18")
        assert any("📢" in line for line in lines) and any("삼성전자" in line for line in lines)

    def test_briefing_empty(self, monkeypatch):
        monkeypatch.setattr(dw, "events_for_day", lambda *a, **kw: [])
        assert _disclosure_section("2026-10-18") == []

    def test_dashboard_card(self):
        html = hd.render_html({"signals": {
            "date": "2026-10-19", "active": [], "scorecard": [],
            "disclosures": {"date": "20261019", "rows": [{
                "grade": "A_생존", "name": "삼성전자", "ticker": "005930",
                "category": "무상증자", "mean5": 4.94, "hit5": 60.4,
            }]},
        }})
        assert "공시 신호 (A/B)" in html and "삼성전자" in html


class TestWiring:

    def test_bootstrap_jobs(self):
        src = (Path(__file__).resolve().parents[2] / "app" / "bootstrap.py").read_text(encoding="utf-8")
        assert src.count("scheduled_disclosure_watch") >= 3, "import + 2 jobs"
        assert "disclosure_watch_am" in src and "disclosure_watch_pm" in src
        assert 'job_count"] = 30' in src
