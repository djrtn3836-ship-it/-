"""기대값 운영 지표 테스트 (2026-10-16 Session 118 — Top 트레이더 방법론 이식).

핵심 개념: 승률이 낮아도 (승률×승평균) > (패률×|패평균|)이면 기대값 플러스.
손익분기 승률 = |패평균| / (승평균 + |패평균|).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analytics.paper_trader import PaperTrader  # noqa: E402


def _rows():
    return [
        {"win": True, "return_pct": 6.0, "horizon": 5, "action": "BUY", "tags": ["t1"]},
        {"win": True, "return_pct": 4.0, "horizon": 5, "action": "BUY", "tags": ["t1"]},
        {"win": False, "return_pct": -2.0, "horizon": 5, "action": "BUY", "tags": []},
        {"win": False, "return_pct": -3.0, "horizon": 5, "action": "BUY", "tags": []},
        {"win": False, "return_pct": -3.0, "horizon": 5, "action": "BUY", "tags": []},
    ]


class TestExpectancy:

    def test_avg_win_loss(self):
        st = PaperTrader._stats(PaperTrader.__new__(PaperTrader), _rows())
        assert st["avg_win"] == 5.0       # (6+4)/2
        assert st["avg_loss"] == pytest_approx(-2.6667)
        # 손익분기: 2.667/(5+2.667) = 34.8% — 실제 승률 40% > 34.8% → 기대값 플러스
        be = abs(st["avg_loss"]) / (st["avg_win"] + abs(st["avg_loss"])) * 100.0
        assert 34.0 < be < 36.0
        assert st["win_rate"] == 40.0

    def test_negative_expectancy_case(self):
        rows = [
            {"win": True, "return_pct": 2.0, "horizon": 5, "action": "BUY"},
            {"win": False, "return_pct": -4.0, "horizon": 5, "action": "BUY"},
            {"win": False, "return_pct": -4.0, "horizon": 5, "action": "BUY"},
            {"win": False, "return_pct": -4.0, "horizon": 5, "action": "BUY"},
        ]
        st = PaperTrader._stats(PaperTrader.__new__(PaperTrader), rows)
        be = abs(st["avg_loss"]) / (st["avg_win"] + abs(st["avg_loss"])) * 100.0
        assert abs(be - 66.7) < 0.1   # 최소 67% 승률 필요 — 실제 25% → 기대값 마이너스
        assert st["avg_return"] < 0

    def test_format_report_shows_breakeven(self, tmp_path):
        sp = tmp_path / "signals.jsonl"
        stp = tmp_path / "settled.jsonl"
        sp.write_text("", encoding="utf-8")
        with open(stp, "w", encoding="utf-8") as f:
            for i, r in enumerate(_rows()):
                r = dict(r, ts="2026-10-15T09:00:00", ticker=f"00000{i}")
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        pt = PaperTrader(db=None, signals_path=sp, settled_path=stp)
        text = pt.format_report(days=3650)
        assert "손익분기 승률" in text and "<b>35%</b>" in text
        assert "승 +5.0%" in text and "패 -2.7%" in text


def pytest_approx(v):
    class _A:
        def __eq__(self, other):
            return abs(other - v) < 0.001

    return _A()
