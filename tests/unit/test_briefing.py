"""종합 브리핑 리포트 테스트 (2026-10-10 Session 108)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class _FakeDB:
    def __init__(self, rows=None, tags=None):
        self._rows = rows or []
        self._tags = tags

    async def get_decisions_by_date(self, day):
        return self._rows

    async def get_tag_win_rates(self):
        if self._tags is None:
            raise RuntimeError("no tag data")
        return self._tags


class _FakePaper:
    def summary(self, days=90):
        return {
            "by_horizon": {"5": {"n": 12, "win_rate": 58.3}},
            "baseline": 44.3,
            "pending": 3,
        }


def _build(db=None, **kw):
    from report.briefing import build_briefing

    return asyncio.run(build_briefing(db or _FakeDB(), **kw))


class TestBriefing:

    def test_morning_header_and_no_crash_with_empty_db(self):
        text = _build(kind="morning")
        assert "아침 종합 브리핑" in text
        assert "시스템" in text or "시장" in text

    def test_evening_header(self):
        assert "저녁 종합 브리핑" in _build(kind="evening")

    def test_signal_summary_counts(self):
        db = _FakeDB(rows=[
            {"action": "SIGNAL_ENTRY"}, {"action": "SIGNAL_ENTRY"},
            {"action": "HOLD"}, {"action": "EVENT_EXIT"},
        ])
        text = _build(db, kind="evening")
        assert "신호" in text and "4건" in text and "SIGNAL_ENTRY 2건" in text

    def test_paper_section_rendered(self):
        text = _build(kind="evening", paper_trader=_FakePaper())
        assert "모의매매" in text and "5일 58%(12)" in text.replace("58.0", "58")

    def test_tag_section_variants(self):
        db = _FakeDB(tags=[{"tag": "flow_both_buy", "n": 9, "win_rate": 0.61}])
        text = _build(db, kind="evening")
        assert "태그 승률" in text and "flow_both_buy 61%(9)" in text.replace("61.0", "61")
        db2 = _FakeDB(tags=[{"tag": "x", "n": 3, "win_rate": 0.5}])  # n<5 → 미표시
        assert "태그 승률" not in _build(db2, kind="evening")

    def test_tag_section_dict_shape(self):
        db = _FakeDB(tags={"fund_solid": {"n": 7, "win_rate": 57.1}})
        text = _build(db, kind="evening")
        assert "fund_solid" in text

    def test_tolerant_to_total_failure(self):
        class _Broken:
            async def get_decisions_by_date(self, day):
                raise RuntimeError("db down")

            async def get_tag_win_rates(self):
                raise RuntimeError("db down")

        text = _build(_Broken(), kind="evening")
        assert "저녁 종합 브리핑" in text  # 부분 브리핑은 나와야 한다

    def test_length_bounded(self):
        class _Big:
            async def get_decisions_by_date(self, day):
                return [{"action": f"X{i}"} for i in range(2000)]

            async def get_tag_win_rates(self):
                return []

        text = _build(_Big(), kind="evening")
        assert len(text) <= 3600


class TestSchedulerRegistration:

    def test_jobs_registered(self):
        p = Path(__file__).resolve().parents[2] / "app" / "bootstrap.py"
        src = p.read_text(encoding="utf-8")
        assert '"morning_briefing"' in src and '"evening_briefing"' in src
        assert "hour=8, minute=35" in src and "hour=17, minute=25" in src
        assert 'self.startup_details["job_count"] = 24' in src
