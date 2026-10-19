"""유형별 라이프사이클 테스트 (2026-10-16 Session 116 — 사용자 승인 설계).

설계: 단타=당일 15:15 청산 / 스윙=5거래일 뒤 15:15 청산 / 청산 후 2거래일 재진입 금지.
배경: 같은 종목 반복 신호(81종목 중 42종목 2일 이상) — 스윙 정의에 어긋남.
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scanner.deep_analyzer import DeepAnalyzer  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]


def _fake_analyzer():
    a = DeepAnalyzer.__new__(DeepAnalyzer)
    a.trailing_stops = {}
    a._reentry_block = {}
    a._lock = asyncio.Lock()
    a.swing_hold_days = 5
    a.reentry_block_days = 2
    return a


class TestExitDeadline:

    def test_intraday_deadline_same_day_1515(self):
        a = _fake_analyzer()
        entry = datetime(2026, 10, 16, 10, 0)  # 금요일
        dl = a._compute_exit_deadline(entry, "단타(추정)")
        assert dl.date() == entry.date()
        assert (dl.hour, dl.minute) == (15, 15)

    def test_intraday_floor_20min(self):
        a = _fake_analyzer()
        entry = datetime(2026, 10, 16, 15, 5)  # 늦은 진입
        dl = a._compute_exit_deadline(entry, "단타(추정)")
        assert (dl.hour, dl.minute) == (15, 25), "최소 보유 20분 플로어 미적용"

    def test_swing_deadline_5_trading_days(self):
        a = _fake_analyzer()
        entry = datetime(2026, 10, 16, 10, 0)  # 금요일 → +5거래일 = 10-23(금)
        dl = a._compute_exit_deadline(entry, "스윙(추정)")
        assert dl.date() == datetime(2026, 10, 23).date(), f"주말 미제외 의심: {dl}"
        assert (dl.hour, dl.minute) == (15, 15)

    def test_unknown_type_defaults_to_swing(self):
        a = _fake_analyzer()
        entry = datetime(2026, 10, 16, 10, 0)
        dl = a._compute_exit_deadline(entry, "")
        assert dl.date() > entry.date(), "유형 미상은 스윙(보수적) 취급"


class TestExpiredAndDue:

    def _st(self, dl, entry_price=10000.0, last=10500.0, side="BUY", ttype="스윙(추정)"):
        return {
            "action": side, "entry_price": entry_price, "last_price": last,
            "entry_time": "2026-10-16T10:00:00", "exit_deadline": dl,
            "trade_type": ttype, "current_stop": 9800.0, "tp_hit_level": 1,
        }

    def test_expired_positions(self):
        a = _fake_analyzer()
        a.trailing_stops["005930"] = self._st("2026-10-16T15:15:00")
        a.trailing_stops["000660"] = self._st("2026-10-23T15:15:00")
        a.trailing_stops["035420"] = self._st("2026-10-16T15:15:00")  # 마감 없음
        a.trailing_stops["035420"].pop("exit_deadline")
        out = a.expired_positions(now_dt=datetime(2026, 10, 16, 15, 20))
        assert len(out) == 1 and out[0]["ticker"] == "005930"
        assert out[0]["action"] == "EVENT_EXIT" and "만료" in out[0]["reason"]
        assert abs(out[0]["pnl"] - 5.0) < 0.01

    def test_due_today(self):
        a = _fake_analyzer()
        a.trailing_stops["005930"] = self._st("2026-10-16T15:15:00")
        a.trailing_stops["000660"] = self._st("2026-10-23T15:15:00")
        due = a.due_today(now_dt=datetime(2026, 10, 16, 9, 0))
        assert len(due) == 1 and due[0]["deadline"] == "15:15"


class TestReentryBlock:

    def test_block_and_expire(self):
        a = _fake_analyzer()
        future = a._add_trading_days(datetime.now(), 2).strftime("%Y-%m-%d")
        a._reentry_block["005930"] = future
        assert a._is_reentry_blocked("005930") is True
        a._reentry_block["005930"] = "2020-01-01"  # 과거
        assert a._is_reentry_blocked("005930") is False
        assert "005930" not in a._reentry_block, "만료 시 정리되어야 함"

    def test_clear_sets_block(self):
        a = _fake_analyzer()
        a.trailing_stops["005930"] = {"action": "BUY", "exit_deadline": "2026-10-16T15:15:00"}
        asyncio.run(a.clear_trailing_stop("005930"))
        assert "005930" in a._reentry_block
        assert a._is_reentry_blocked("005930") is True


class TestWiring:

    def test_bootstrap_wiring(self):
        src = (_ROOT / "app" / "bootstrap.py").read_text(encoding="utf-8")
        assert "await analyzer.set_position_policy(" in src, "진입 시 정책 설정 누락"
        assert "self._exit_sweeper_task = asyncio.create_task" in src
        assert "async def _exit_sweeper_loop" in src
        assert "EXIT_DEADLINE_HIT" in src
        assert "exit_soon=exit_soon" in src

    def test_entry_checks_reentry_block(self):
        src = (_ROOT / "scanner" / "deep_analyzer.py").read_text(encoding="utf-8")
        assert "not self._is_reentry_blocked(ticker)" in src, "재진입 금지가 진입 조건에 미배선"

    def test_briefing_renders_exit_soon(self):
        from report.briefing import build_briefing

        class _DB:
            async def get_decisions_by_date(self, day):
                return []

            async def get_tag_win_rates(self):
                return []

        text = asyncio.run(
            build_briefing(_DB(), kind="morning", exit_soon=["코웨이 (021240) 스윙(추정) — 15:15 청산 예정"])
        )
        assert "오늘 청산 예정" in text and "코웨이" in text
