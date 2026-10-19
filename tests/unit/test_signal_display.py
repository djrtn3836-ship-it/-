# -*- coding: utf-8 -*-
"""신호 기록부 표시(S139) 테스트 — 브리핑 🔔 + 대시보드 카드."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scheduler.signal_logger as sl  # noqa: E402
from report import html_dashboard as hd  # noqa: E402
from report.briefing import _signal_registry_section  # noqa: E402


class TestLatestSignals:

    def test_latest_by_date_and_sort(self, tmp_path):
        p = tmp_path / "log.jsonl"
        rows = [
            {"date": "2026-10-18", "signal": "a", "grade": "A", "label": "x"},
            {"date": "2026-10-19", "signal": "b", "grade": "C", "label": "y"},
            {"date": "2026-10-19", "signal": "c", "grade": "A", "label": "z"},
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        out = sl.latest_signals(log_path=p)
        assert out["date"] == "2026-10-19" and len(out["signals"]) == 2
        assert [r["signal"] for r in out["signals"]] == ["c", "b"], "등급 A 먼저"

    def test_day_param(self, tmp_path):
        p = tmp_path / "log.jsonl"
        rows = [
            {"date": "2026-10-18", "signal": "a", "grade": "A", "label": "x"},
            {"date": "2026-10-19", "signal": "b", "grade": "C", "label": "y"},
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        assert sl.latest_signals(log_path=p, day="2026-10-18")["signals"][0]["signal"] == "a"

    def test_empty(self, tmp_path):
        assert sl.latest_signals(log_path=tmp_path / "none.jsonl") == {}


class TestBriefingSection:

    def test_renders_latest_and_pending(self, monkeypatch):
        monkeypatch.setattr(sl, "latest_signals", lambda **kw: {
            "date": "2026-10-19", "signals": [{"grade": "A", "label": "괴리"}],
        })
        monkeypatch.setattr(sl, "scorecard", lambda **kw: [])
        lines = asyncio.run(_signal_registry_section())
        assert any("🔔" in line for line in lines)
        assert any("괴리" in line for line in lines)
        assert any("채점 대기" in line for line in lines)

    def test_renders_scorecard(self, monkeypatch):
        monkeypatch.setattr(sl, "latest_signals", lambda **kw: {"date": "2026-10-19", "signals": []})
        monkeypatch.setattr(sl, "scorecard", lambda **kw: [{"label": "폭≤15", "n": 3, "avg": 2.1, "hit": 66.7}])
        lines = asyncio.run(_signal_registry_section())
        assert any("성적(5일)" in line and "폭≤15" in line for line in lines)

    def test_failure_returns_empty(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("x")

        monkeypatch.setattr(sl, "latest_signals", boom)
        monkeypatch.setattr(sl, "scorecard", boom)
        assert asyncio.run(_signal_registry_section()) == [], "실패 시 섹션 생략(부분 브리핑)"


class TestDashboard:

    def test_collect(self, monkeypatch):
        monkeypatch.setattr(sl, "latest_signals", lambda **kw: {
            "date": "2026-10-19", "signals": [{"grade": "A", "label": "괴리"}],
        })
        monkeypatch.setattr(sl, "scorecard", lambda **kw: [{"label": "폭≤15", "n": 0, "avg": 0.0, "hit": 0.0}])
        out = hd.collect_signal_registry_section()
        assert out["date"] == "2026-10-19" and out["active"][0]["label"] == "괴리"

    def test_render_card(self):
        html = hd.render_html({
            "signals": {
                "date": "2026-10-19",
                "active": [{"grade": "A", "label": "괴리"}],
                "scorecard": [],
            },
        })
        assert "시장 신호 기록부" in html and "괴리" in html and "채점 대기" in html

    def test_render_without_signals(self):
        assert "시장 신호 기록부" in hd.render_html({}), "데이터 없어도 카드 생략/안전"


class TestWiring:

    def test_briefing_wired(self):
        src = (Path(__file__).resolve().parents[2] / "report" / "briefing.py").read_text(encoding="utf-8")
        assert "_signal_registry_section()" in src and "latest_signals" in src

    def test_dashboard_wired(self):
        src = (Path(__file__).resolve().parents[2] / "report" / "html_dashboard.py").read_text(encoding="utf-8")
        assert 'data["signals"] = collect_signal_registry_section()' in src
