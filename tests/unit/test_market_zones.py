# -*- coding: utf-8 -*-
"""시장 구역(S136 ③) 테스트."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analytics.market_zones import (  # noqa: E402
    ZONE_BLOCK,
    ZONE_NORMAL,
    ZONE_OPPORTUNITY,
    ZONE_WATCH,
    classify_zone,
    format_zone_line,
)


class TestClassify:

    def test_crash_blocks(self):
        assert classify_zone(-5.0, 40.0)["zone"] == ZONE_BLOCK
        assert classify_zone(-3.0, 40.0)["zone"] == ZONE_BLOCK, "경계 포함(−3.0)"
        assert classify_zone(-5.0, 12.0)["zone"] == ZONE_BLOCK, "급락 차단이 기회보다 우선"

    def test_panic_is_opportunity_even_while_declining(self):
        z = classify_zone(-1.0, 12.0)
        assert z["zone"] == ZONE_OPPORTUNITY, "S136: '폭≤15&하락중'도 반등 생존 후보"
        z2 = classify_zone(0.5, 12.0)
        assert z2["zone"] == ZONE_OPPORTUNITY
        assert "2022" in z2["reasons"][0], "국면 의존 명시"

    def test_watch_by_breadth(self):
        assert classify_zone(0.5, 18.0)["zone"] == ZONE_WATCH

    def test_watch_by_trend(self):
        assert classify_zone(-2.0, 40.0)["zone"] == ZONE_WATCH

    def test_normal(self):
        assert classify_zone(0.5, 40.0)["zone"] == ZONE_NORMAL
        assert classify_zone(None, None)["zone"] == ZONE_NORMAL, "데이터 없음 → 정상(추정 금지)"


class TestFormat:

    def test_line_contains_zone_and_ref(self):
        line = format_zone_line(classify_zone(-4.0, 30.0), -4.0, 30.0)
        assert "구역" in line and "진입금지" in line and "참고용" in line


class TestWiring:

    def test_briefing_uses_zones(self):
        src = (Path(__file__).resolve().parents[2] / "report" / "briefing.py").read_text(encoding="utf-8")
        assert "market_zones" in src and "classify_zone" in src and "format_zone_line" in src

    def test_dashboard_uses_zones(self):
        src = (Path(__file__).resolve().parents[2] / "report" / "html_dashboard.py").read_text(encoding="utf-8")
        assert "classify_zone" in src and "🚦 구역" in src
