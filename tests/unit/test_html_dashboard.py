# -*- coding: utf-8 -*-
"""tests/unit/test_html_dashboard.py - 운영 대시보드 생성 검증 (P6-4)."""

from pathlib import Path

import pytest

from report import html_dashboard as hd


def _raw_tags_balanced(html: str) -> bool:
    """렌더된 문서에 이스케이프되지 않은 script/img 태그가 없어야 한다."""
    lowered = html.lower()
    return "onerror" not in lowered and html.count("<script") == 0


def _sample(**over: object) -> dict:
    data = {
        "system": {"universe_source": "csv", "csv_age_days": 0.9, "jobs": 14},
        "data": {"decisions": 120, "outcomes": 60, "ohlcv": 230441, "progress": 0.24, "ml_ready": False},
        "paper": {"evaluated": 0, "pending": 20, "win_rate": 0.0, "avg_return": 0.0},
        "monitor": {
            "quality": {"signals_observed": 100, "anomalies": 3, "anomaly_rate": 0.03,
                        "alerts_sent": 2, "suppression_ratio": 0.33, "errors": 0,
                        "hints": ["알림 억제 비율 높음"], "thresholds": {"anomaly_threshold": 0.65}},
            "circuit_breakers": {"is_open": False},
        },
        "macro": {"score": 0.48, "indicators": {"kospi_trend": {"raw": -1.5, "score": 0.35}}},
    }
    data.update(over)
    return data


class TestRendering:
    def test_contains_all_sections(self) -> None:
        html = hd.render_html(_sample())

        for title in ("시스템 상태", "데이터 축적", "감시 · 알림", "모멘텀 참고 신호", "매크로", "전략 검증 결론"):
            assert title in html, f"{title} 섹션 누락"

    def test_standalone_html_document(self) -> None:
        html = hd.render_html(_sample())

        assert html.startswith("<!DOCTYPE html>")
        assert html.rstrip().endswith("</html>")
        assert "<style>" in html and "<script" not in html     # 서버/스크립트 없음

    def test_utf8_meta_declared(self) -> None:
        assert 'charset="utf-8"' in hd.render_html(_sample())

    def test_shows_readiness_progress(self) -> None:
        html = hd.render_html(_sample())
        assert "24%" in html

    def test_fallback_badge_is_red(self) -> None:
        html = hd.render_html(_sample(system={"universe_source": "fallback", "jobs": 13}))
        assert "폴백(위험)" in html

    def test_stale_badge_warns(self) -> None:
        html = hd.render_html(_sample(system={"universe_source": "csv_stale", "csv_age_days": 30.0}))
        assert "CSV 스테일" in html

    def test_open_circuit_breaker_is_badged(self) -> None:
        data = _sample()
        data["monitor"]["circuit_breakers"] = {"is_open": True}
        assert "OPEN(차단)" in hd.render_html(data)

    def test_alpha_verdict_stated(self) -> None:
        html = hd.render_html(_sample())
        assert "부결" in html and "−4.8%" in html

    def test_hints_rendered(self) -> None:
        assert "알림 억제 비율 높음" in hd.render_html(_sample())


class TestSafetyAndRobustness:
    def test_escapes_injected_html(self) -> None:
        """외부 문자열이 HTML로 주입되면 안 된다."""
        data = _sample(system={"universe_source": "<img src=x onerror=alert(1)>", "jobs": 1})
        html = hd.render_html(data)

        assert "<img src=x" not in html          # 알 수 없는 소스는 배지로 대체(원문 미출력)
        assert _raw_tags_balanced(html)

    def test_escapes_html_in_free_text(self) -> None:
        """자유 문자열 경로(힌트/매크로 키)는 escape되어야 한다."""
        data = _sample()
        data["monitor"]["quality"]["hints"] = ["<script>alert(1)</script>"]
        data["macro"]["indicators"] = {"<b>evil</b>": {"raw": 1.0, "score": 0.5}}

        html = hd.render_html(data)

        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html
        assert "<b>evil</b>" not in html
        assert "&lt;b&gt;evil&lt;/b&gt;" in html

    def test_renders_with_empty_data(self) -> None:
        html = hd.render_html({})
        assert "<!DOCTYPE html>" in html

    def test_renders_with_none_values(self) -> None:
        data = _sample(paper={"evaluated": None, "pending": None, "win_rate": None})
        html = hd.render_html(data)
        assert "—" in html

    def test_macro_score_rendered(self) -> None:
        assert "0.480" in hd.render_html(_sample())


class TestGeneration:
    async def test_generate_writes_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        class _DB:
            async def _execute_read(self, q, p=()):
                return [{"n": 0}]

            async def get_momentum_paper_stats(self):
                return {"evaluated": 0, "pending": 0}

            async def close(self):
                return None

        monkeypatch.setattr("data.db_manager.DatabaseManager", _DB)
        monkeypatch.setattr(hd, "collect_monitor_section", lambda: {})
        monkeypatch.setattr(hd, "collect_macro_section", lambda: {})

        out = tmp_path / "d.html"
        path = await hd.generate_dashboard(out)

        assert path == out
        assert out.exists()
        assert "운영 대시보드" in out.read_text(encoding="utf-8")

    async def test_scheduled_returns_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _gen(out_path: Path = hd.DEFAULT_OUT) -> Path:
            out_path.write_text("x", encoding="utf-8")
            return out_path

        monkeypatch.setattr(hd, "generate_dashboard", _gen)
        result = await hd.scheduled_dashboard()

        assert result["status"] == "ok"
