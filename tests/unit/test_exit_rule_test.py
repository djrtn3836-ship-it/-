# -*- coding: utf-8 -*-
"""매도규칙(청산) 검증 도구 테스트 (S136 ②)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analytics.market_breadth import S  # noqa: E402
from validation.exit_rule_test import find_events, simulate, stats_line  # noqa: E402


def _S(closes, highs=None, lows=None, opens=None, vols=None) -> S:
    n = len(closes)
    dates = [f"2026-01-{i + 1:02d}" for i in range(n)]
    return S(
        dates,
        [float(c) for c in closes],
        [float(h) for h in (highs or closes)],
        [float(v) for v in (lows or closes)],
        [float(o) for o in (opens or closes)],
        [float(v) for v in (vols or [100.0] * n)],
    )


class TestSimulate:

    def test_fixed_hold(self):
        s = _S([100, 101, 102, 103], opens=[100, 100, 101, 102])
        r = simulate(s.close, s.high, s.low, s.open, 0, "고정 3일")
        assert r is not None and abs(r[0] - 1.7) < 1e-9, "3일째 종가(102/100=+2) − 0.3 비용"

    def test_trailing_exit(self):
        s = _S([100, 106, 110, 104, 103], opens=[100] * 5)
        r = simulate(s.close, s.high, s.low, s.open, 0, "트레일링-5%")
        assert r is not None and abs(r[0] - 3.7) < 1e-9, "고점 110→104(−5.5%) 이탈 종가 청산"

    def test_target_and_stop_priority(self):
        s = _S([100, 100], highs=[100, 108], lows=[100, 96], opens=[100, 100])
        r = simulate(s.close, s.high, s.low, s.open, 0, "목표7/손절3")
        assert r is not None and abs(r[0] - (-3.3)) < 1e-9, "같은 날 동시 터치 → 손절 우선(보수)"
        s2 = _S([100, 101], highs=[100, 108], lows=[100, 99], opens=[100, 100])
        r2 = simulate(s2.close, s2.high, s2.low, s2.open, 0, "목표7/손절3")
        assert r2 is not None and abs(r2[0] - 6.7) < 1e-9, "+7% 목표 도달"

    def test_ma20_exit(self):
        closes = [100.0] * 21 + [99.0, 98.0]
        s = _S(closes, opens=[100.0] * 23)
        r = simulate(s.close, s.high, s.low, s.open, 20, "MA20이탈")
        assert r is not None and abs(r[0] - (-1.3)) < 1e-9, "99 < MA20(100) → 99 청산"

    def test_no_entry_open(self):
        s = _S([100, 101], opens=[0, 100])
        assert simulate(s.close, s.high, s.low, s.open, 0, "고정 3일") is None


class TestEvents:

    def test_event_and_dedupe(self):
        n = 80  # 최소 60봉 요건
        closes = [100.0] * n
        closes[45] = 103.0     # +3% 급등
        closes[46] = 106.0     # 연속 → 5일 간격 제외
        vols = [100.0] * n
        vols[45] = 500.0
        vols[46] = 500.0
        s = _S(closes, vols=vols)
        events = find_events({"A": s})
        assert [e[1] for e in events] == [46], "첫 이벤트만(진입일=신호일+1)"

    def test_artifact_skip(self):
        n = 80
        closes = [100.0] * n
        closes[45] = 103.0
        closes[50] = 160.0     # 창 안 ±31% 초과(분할류)
        vols = [100.0] * n
        vols[45] = 500.0
        s = _S(closes, vols=vols)
        assert find_events({"A": s}) == []


class TestStats:

    def test_stats_line(self):
        st = stats_line([2.0, -1.0, 3.0, -1.0])
        assert st["n"] == 4 and st["win"] == 50.0 and abs(st["avg_w"] - 2.5) < 1e-9
