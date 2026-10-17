"""수급 5년 백필 테스트 (2026-10-16 Session 119 — 사용자 승인, 주말 실행).

핵심 계약:
- 주중 날짜만 요청, 응답 봉 날짜가 요청 날짜와 일치할 때만 저장(휴장일 오라벨 방지)
- 요청 1건마다 체크포인트 → 중단/재시작 시 이어서(이미 저장된 날짜 재요청 없음)
- 연속 실패 시 재로그인 1회 시도, 임계 도달 시 중단(API 보호)
- 장중에는 일시정지
"""
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scheduler.supply_backfill import (  # noqa: E402
    _is_market_hours,
    _weekdays_desc,
    backfill,
)

START = date(2026, 10, 5)   # 월
END = date(2026, 10, 16)    # 금


class _Stop(Exception):
    pass


class FakeKiwoom:
    """요청 날짜 그대로 응답. 휴장일(holiday)은 직전 봉 날짜를 반환(불일치 시나리오)."""

    def __init__(self, holiday=(), always_error=False):
        self.requests = []
        self.holiday = set(holiday)
        self.always_error = always_error
        self.reconnects = 0

    async def request_tr(self, ticker, tr_type, dt=None):
        self.requests.append((ticker, dt))
        if self.always_error:
            return {"error": "rate limit"}
        d = date.fromisoformat(f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}")
        if d in self.holiday:
            prev = d
            while True:
                prev = date.fromordinal(prev.toordinal() - 1)
                if prev.weekday() < 5 and prev not in self.holiday:
                    break
            return {"date": prev.isoformat(), "foreign_net": 1.0, "inst_net": 2.0}
        return {
            "date": d.isoformat(),
            "foreign_net": float(d.day) * 100,
            "inst_net": float(d.day) * -50,
        }

    async def connect(self):
        self.reconnects += 1
        return True


class FakeDB:
    def __init__(self):
        self.rows = []

    async def save_supply_demand(self, records):
        self.rows.extend(records)
        return len(records)


async def _fast_sleep(t):
    return None


def _weekdays(start, end):
    return [d for d in _weekdays_desc(start, end)]


class TestWeekdays:

    def test_desc_no_weekend(self):
        days = list(_weekdays_desc(START, END))
        assert days[0] == END and days[-1] == START
        assert all(d.weekday() < 5 for d in days)
        assert date(2026, 10, 11) not in days  # 일요일 제외
        assert len(days) == 10  # 10-05~10-16 평일 10일


class TestMarketPause:

    def test_market_hours(self):
        assert _is_market_hours(datetime(2026, 10, 16, 10, 0)) is True
        assert _is_market_hours(datetime(2026, 10, 16, 8, 0)) is False
        assert _is_market_hours(datetime(2026, 10, 17, 10, 0)) is False  # 토요일


class TestBackfill:

    def test_basic_saves_only_weekdays(self, tmp_path):
        k, db = FakeKiwoom(), FakeDB()
        cp = tmp_path / "state.json"
        res = asyncio.run(backfill(
            k, db, ["005930"], start_date=START, end_date=END,
            delay=0, checkpoint_path=cp, market_pause=False, sleep=_fast_sleep,
        ))
        assert res["saved"] == 10 and res["requests"] == 10
        assert all(r["foreign_net"] is not None for r in db.rows)
        dates = {r["date"] for r in db.rows}
        assert "2026-10-11" not in dates and "2026-10-10" not in dates

    def test_holiday_mismatch_skipped(self, tmp_path):
        k = FakeKiwoom(holiday={date(2026, 10, 9)})  # 금요일 휴장
        db = FakeDB()
        res = asyncio.run(backfill(
            k, db, ["005930"], start_date=START, end_date=END,
            delay=0, checkpoint_path=tmp_path / "s.json",
            market_pause=False, sleep=_fast_sleep,
        ))
        assert res["skipped"] == 1
        assert res["saved"] == 9
        assert "2026-10-09" not in {r["date"] for r in db.rows}

    def test_checkpoint_resume(self, tmp_path):
        cp = tmp_path / "state.json"
        k1 = FakeKiwoom()

        async def stop_after_5(t):
            if t == 0 and len(k1.requests) >= 5:
                raise _Stop()
            return None

        try:
            asyncio.run(backfill(
                k1, FakeDB(), ["005930"], start_date=START, end_date=END,
                delay=0, checkpoint_path=cp, market_pause=False, sleep=stop_after_5,
            ))
            raise AssertionError("중단되어야 함")
        except _Stop:
            pass
        assert cp.exists(), "체크포인트 저장 필수"
        state = json.loads(cp.read_text(encoding="utf-8"))
        assert state["ticker_idx"] == 0 and state["next_date"] < END.isoformat()

        k2, db2 = FakeKiwoom(), FakeDB()
        res = asyncio.run(backfill(
            k2, db2, ["005930"], start_date=START, end_date=END,
            delay=0, checkpoint_path=cp, market_pause=False, sleep=_fast_sleep,
        ))
        assert res["saved"] == 6, "재개 후 남은 6일 저장"
        # 재개 후: 이미 처리한 날짜(체크포인트 저장분)는 재요청하지 않음
        #   (중단 시점의 마지막 1건은 저장 전이었을 수 있어 최대 1건 중복 허용 — 멱등)
        assert len(k2.requests) <= 6
        all_req = [r[1] for r in k1.requests] + [r[1] for r in k2.requests]
        assert len(set(all_req)) == 10, "모든 날짜가 정확히 한 번 이상 커버되어야"
        assert len(all_req) <= 11, "중복은 최대 1건(재개 경계)만 허용"

    def test_error_streak_aborts(self, tmp_path):
        k = FakeKiwoom(always_error=True)
        res = asyncio.run(backfill(
            k, FakeDB(), ["005930"], start_date=START, end_date=END,
            delay=0, checkpoint_path=tmp_path / "s.json", market_pause=False,
            sleep=_fast_sleep, max_consecutive_errors=3, reconnect_after=2,
        ))
        assert res["aborted"] is True
        assert res["errors"] == 3
        assert k.reconnects == 1, "임계 전 재로그인 1회 시도"

    def test_no_data_is_skip_not_error(self, tmp_path):
        """상장 전 날짜의 no_data가 오류 30연속으로 오판돼 중단되던 사고의 회귀 테스트."""
        from datetime import date as _date

        class _NoDataK:
            async def request_tr(self, ticker, tr_type, dt=None):
                if dt < "20240617":
                    return {"error": "no_data"}  # 상장 전(시맨틱)
                d = _date.fromisoformat(f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}")
                return {"date": d.isoformat(), "foreign_net": 1.0, "inst_net": 1.0}

        db = FakeDB()
        res = asyncio.run(backfill(
            _NoDataK(), db, ["062040"],
            start_date=date(2024, 1, 1), end_date=date(2024, 7, 1),
            delay=0, checkpoint_path=tmp_path / "s.json",
            market_pause=False, sleep=_fast_sleep,
        ))
        assert res["aborted"] is False, "no_data는 중단 사유가 아님"
        assert res["errors"] == 0
        assert res["saved"] == 11, "2024-06-17~07-01 평일 11일만 저장"
        assert res["skipped"] > 100, "상장 전 날짜는 전부 스킵"

    def test_ticker_transition_checkpoint(self, tmp_path):
        cp = tmp_path / "s.json"
        k, db = FakeKiwoom(), FakeDB()
        asyncio.run(backfill(
            k, db, ["005930", "000660"], start_date=START, end_date=END,
            delay=0, checkpoint_path=cp, market_pause=False, sleep=_fast_sleep,
        ))
        state = json.loads(cp.read_text(encoding="utf-8"))
        assert state["ticker_idx"] == 2, "완료 후 다음 종목 인덱스"
        assert len(db.rows) == 20
