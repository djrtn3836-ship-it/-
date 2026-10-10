"""모의매매 측정 시스템 테스트 (2026-10-10).

사용자 최대 요구사항("80% 확률 5~10% 수익")은 달성 불가능하지만,
"무작위 기준선(44.3%) 대비 초과 승률이 있는가"는 측정할 수 있다.
이 테스트는 그 측정기가 **추정 없이 실현가만으로** 동작함을 보장한다.
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analytics.paper_trader import (  # noqa: E402
    BASELINE_UP_PROB, HORIZONS, PaperTrader, _wilson_ci,
)


class _FakeDB:
    """지정한 종가 시퀀스를 돌려주는 가짜 DB."""

    def __init__(self, closes):
        self._closes = closes

    async def get_ohlcv(self, _ticker, _days):
        return [{"date": f"2026-10-{i+1:02d}", "close": c} for i, c in enumerate(self._closes)]


# 가짜 DB의 날짜(2026-10-01~)와 맞춘 고정 진입 시각
_TS = datetime(2026, 10, 1, 10, 0, 0).timestamp()


def _trader(tmp_path, closes=None, db=True):
    return PaperTrader(
        db=_FakeDB(closes) if db else None,
        signals_path=tmp_path / "signals.jsonl",
        settled_path=tmp_path / "settled.jsonl",
    )


# ── 기록 ──────────────────────────────────────────────────────────────
class TestRecord:

    def test_tradeable_actions_recorded(self, tmp_path):
        t = _trader(tmp_path)
        for act in ("SIGNAL_ENTRY", "BUY", "SELL"):
            assert t.record_signal("005930", act, 100.0, ts=_TS) is True
        assert len(t._load_jsonl(t.signals_path)) == 3

    def test_hold_and_bad_price_ignored(self, tmp_path):
        t = _trader(tmp_path)
        assert t.record_signal("005930", "HOLD", 100.0, ts=_TS) is False
        assert t.record_signal("005930", "BUY", 0.0, ts=_TS) is False
        assert t._load_jsonl(t.signals_path) == []


# ── 채점 ──────────────────────────────────────────────────────────────
class TestSettle:

    def test_settles_all_horizons_with_real_prices(self, tmp_path):
        closes = [100.0] * 15
        closes[1] = 105.0    # 1일 뒤 +5%
        closes[3] = 110.0    # 3일 뒤 +10%
        t = _trader(tmp_path, closes)
        t.record_signal("005930", "SIGNAL_ENTRY", 100.0, ts=_TS)
        settled = asyncio.run(t.settle_pending())
        assert settled == len(HORIZONS)
        rows = {r["horizon"]: r for r in t._load_jsonl(t.settled_path)}
        assert rows[1]["return_pct"] == pytest.approx(5.0)
        assert rows[3]["return_pct"] == pytest.approx(10.0)
        assert rows[1]["win"] is True
        assert rows[5]["return_pct"] == pytest.approx(0.0)
        assert rows[5]["win"] is False

    def test_not_matured_stays_pending(self, tmp_path):
        closes = [100.0]      # 진입일 데이터만 존재 → 아직 채점 불가
        t = _trader(tmp_path, closes)
        t.record_signal("005930", "BUY", 100.0, ts=_TS)
        settled = asyncio.run(t.settle_pending())
        assert settled == 0
        pending = t._load_jsonl(t.signals_path)
        assert len(pending) == 1
        assert pending[0]["settled_horizons"] == []   # 아직 아무 지평도 확정 안 됨

    def test_partial_settlement_keeps_record_until_all_horizons(self, tmp_path):
        closes = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]   # 5일까지 가능
        t = _trader(tmp_path, closes)
        t.record_signal("005930", "BUY", 100.0, ts=_TS)
        asyncio.run(t.settle_pending())
        pending = t._load_jsonl(t.signals_path)
        assert len(pending) == 1
        assert pending[0]["settled_horizons"] == [1, 3, 5]
        assert len(t._load_jsonl(t.settled_path)) == 3

    def test_no_db_is_noop(self, tmp_path):
        t = _trader(tmp_path, db=False)
        t.record_signal("005930", "BUY", 100.0, ts=_TS)
        assert asyncio.run(t.settle_pending()) == 0

    def test_settlement_is_reproducible(self, tmp_path):
        first = _trader(tmp_path, [100.0, 103.0] * 8)
        first.record_signal("005930", "BUY", 100.0, ts=_TS)
        asyncio.run(first.settle_pending())
        second = _trader(tmp_path, [100.0, 103.0] * 8)
        second.record_signal("005930", "BUY", 100.0, ts=_TS)
        asyncio.run(second.settle_pending())
        assert first._load_jsonl(first.settled_path) == second._load_jsonl(second.settled_path)


# ── 통계 ──────────────────────────────────────────────────────────────
class TestStats:

    def test_wilson_ci_bounds(self):
        lo, hi = _wilson_ci(50, 100)
        assert 38 < lo < 42 and 58 < hi < 62      # 50% 승률 → 약 40~60%
        assert _wilson_ci(0, 0) == (0.0, 0.0)
        lo_small, hi_small = _wilson_ci(5, 5)
        assert lo_small > 40 and hi_small == 100.0

    def test_summary_and_baseline_comparison(self, tmp_path):
        closes = [100.0] * 15
        closes[5] = 106.0     # +6% (승리)
        t = _trader(tmp_path, closes)
        t.record_signal("005930", "SIGNAL_ENTRY", 100.0, tags=["sent_pos"], ts=_TS)
        asyncio.run(t.settle_pending())
        s = t.summary(days=90)
        assert s["settled"] == len(HORIZONS)
        h5 = s["by_horizon"][5]
        assert h5["n"] == 1 and h5["win_rate"] == 100.0
        assert h5["ci_low"] < BASELINE_UP_PROB, "소표본은 기준선 초과로 판정하면 안 됨"
        assert h5["beats_baseline"] is False
        assert "sent_pos" in s["by_tag"] or s["by_tag"] == {}

    def test_report_text_has_disclaimer_for_small_sample(self, tmp_path):
        t = _trader(tmp_path, [100.0] * 15)
        t.record_signal("005930", "BUY", 100.0, ts=_TS)
        asyncio.run(t.settle_pending())
        report = t.format_report(days=90)
        assert "모의매매 성과" in report
        assert "기준선" in report
        assert "표본 부족" in report      # 30건 미만 경고
