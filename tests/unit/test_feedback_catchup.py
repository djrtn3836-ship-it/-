# -*- coding: utf-8 -*-
"""자동학습 캐치업 테스트 (S134).

배경(실측 버그):
    ① 학습기가 '어제 하루'만 처리 → 앱이 하루 꺼지면 그날 outcomes 영구 누락
       (10-14·10-16 배치 누락 실측).
    ② return_5d가 실제로는 1일 값 — ohlcv 최신 종가를 1d·5d에 같이 사용(오염).
수정: 최근 10일 내 미확정 전부 재정산 + 결정일 고정 봉(다음 봉=1d, +5봉=5d),
      5d 미성숙은 None 저장 → 다음 실행에서 자동 재정산.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import feedback.feedback_learner as fb  # noqa: E402
from feedback.feedback_learner import FeedbackLearner  # noqa: E402


def _D(off: int) -> str:
    return (date.today() + timedelta(days=off)).isoformat()


def _rows(offsets_prices) -> list:
    return [{"date": _D(o), "close": p} for o, p in offsets_prices]


class FakeDB:
    def __init__(self, unsettled=None, ohlcv=None):
        self.unsettled = unsettled or []
        self.ohlcv = ohlcv or {}
        self.saved = []
        self.window = None
        self.weights = {"momentum": 1.0, "volume": 1.0}
        self.updated_weights = 0

    async def get_unsettled_decisions(self, start, end):
        self.window = (start, end)
        return list(self.unsettled)

    async def get_ohlcv(self, ticker, period=60):
        return list(self.ohlcv.get(ticker, []))

    async def save_outcome(self, outcome):
        self.saved.append(outcome)

    async def flush_pending(self):
        pass  # S137: 대량 쓰기 후 커밋 훅

    async def get_weights(self):
        return dict(self.weights)

    async def update_weight(self, factor, weight):
        self.weights[factor] = weight
        self.updated_weights += 1


class FakeTelegram:
    def __init__(self):
        self.msgs = []

    async def send_raw(self, msg):
        self.msgs.append(msg)


def _learner(db: FakeDB) -> FeedbackLearner:
    fl = FeedbackLearner(db_manager=db)  # type: ignore[arg-type]
    fl.telegram = FakeTelegram()  # type: ignore[assignment]
    fl._xgb = None                # 학습 경로 비활성(단위 테스트)
    fl._xgb_model = None
    return fl


def _dec(dec_id=1, ticker="005930", action="BUY", price=1000.0, created=None):
    return {
        "id": dec_id,
        "ticker": ticker,
        "action": action,
        "price_at_decision": price,
        "created_at": created or f"{_D(-3)} 05:00:00",
        "strategy_scores": None,
    }


SERIES_9 = _rows([(-6, 970.0), (-5, 980.0), (-4, 990.0), (-3, 1000.0), (-2, 1010.0),
                  (-1, 1020.0), (0, 1030.0), (1, 1040.0), (2, 1050.0)])


class TestAnchoredPrices:

    async def test_1d_and_5d_are_anchored(self):
        db = FakeDB(ohlcv={"005930": SERIES_9})
        fl = _learner(db)
        out = await fl._fetch_real_outcome(_dec(), {})
        assert out is not None
        assert abs(out["return_1d"] - 1.0) < 1e-9, "다음 봉(1010/1000) = +1%"
        assert abs(out["return_5d"] - 5.0) < 1e-9, "+5봉(1050/1000) = +5% — 1d 복제 금지"
        assert out["is_correct"] is True

    async def test_5d_pending_is_none(self):
        db = FakeDB(ohlcv={"005930": SERIES_9[:6]})  # -6..-1 (5d 봉 없음)
        fl = _learner(db)
        out = await fl._fetch_real_outcome(_dec(), {})
        assert out is not None and out["return_5d"] is None
        assert abs(out["return_1d"] - 1.0) < 1e-9

    async def test_no_next_bar_skipped(self):
        db = FakeDB(ohlcv={"005930": SERIES_9[:7]})  # -6..0, 결정일=D(0)=마지막
        fl = _learner(db)
        out = await fl._fetch_real_outcome(_dec(created=f"{_D(0)} 05:00:00"), {})
        assert out is None, "1일차 봉 없음 → 다음 캐치업 재시도"

    async def test_caches_ohlcv_per_ticker(self):
        calls = {"n": 0}

        class CountingDB(FakeDB):
            async def get_ohlcv(self, ticker, period=60):
                calls["n"] += 1
                return list(self.ohlcv.get(ticker, []))

        db = CountingDB(ohlcv={"005930": SERIES_9})
        fl = _learner(db)
        cache = {}
        await fl._fetch_real_outcome(_dec(dec_id=1), cache)
        await fl._fetch_real_outcome(_dec(dec_id=2), cache)
        assert calls["n"] == 1, "티커당 1회만 조회(캐시)"

    async def test_is_correct_semantics(self):
        down = _rows([(-6, 1030.0), (-5, 1020.0), (-4, 1010.0), (-3, 1000.0), (-2, 990.0),
                      (-1, 980.0), (0, 970.0), (1, 960.0), (2, 950.0)])
        fl = _learner(FakeDB(ohlcv={"A": down}))
        out = await fl._fetch_real_outcome(_dec(ticker="A", action="BUY"), {})
        assert out is not None and out["is_correct"] is False, "BUY 하락 = 오답"
        out2 = await fl._fetch_real_outcome(_dec(ticker="A", action="SELL"), {})
        assert out2 is not None and out2["is_correct"] is True, "SELL 하락 = 정답"
        out3 = await fl._fetch_real_outcome(_dec(ticker="A", action="HOLD", price=990.0), {})
        assert out3 is not None and out3["is_correct"] is True, "HOLD ±2% 내 = 정답"


class TestCatchupRun:

    async def test_window_is_last_10_days(self, monkeypatch):
        monkeypatch.setattr(fb, "is_trading_day", lambda d: True)
        db = FakeDB(unsettled=[])
        fl = _learner(db)
        await fl.run()
        assert db.window is not None
        start, end = db.window
        assert start == _D(-10) and end == _D(-1), "어제 하루가 아니라 10일 창"

    async def test_settles_and_reports(self, monkeypatch):
        monkeypatch.setattr(fb, "is_trading_day", lambda d: True)
        db = FakeDB(unsettled=[_dec()], ohlcv={"005930": SERIES_9})
        fl = _learner(db)
        await fl.run()
        assert len(db.saved) == 1 and abs(db.saved[0]["return_5d"] - 5.0) < 1e-9
        assert db.updated_weights > 0, "가중치 갱신 경로 실행"
        assert fl.telegram.msgs and "~" in fl.telegram.msgs[-1], "기간 라벨로 보고"

    async def test_holiday_only_window_skips(self, monkeypatch):
        monkeypatch.setattr(fb, "is_trading_day", lambda d: False)
        db = FakeDB(unsettled=[_dec()])
        fl = _learner(db)
        await fl.run()
        assert db.saved == [] and db.window is None


class TestStats:

    async def test_mixed_none_5d(self):
        fl = _learner(FakeDB())
        stats = await fl._generate_stats([
            {"return_1d": 1.0, "return_5d": 2.0, "is_correct": True},
            {"return_1d": -1.0, "return_5d": None, "is_correct": False},
        ])
        assert abs(stats["avg_return_5d"] - 2.0) < 1e-9, "None 제외 평균"

    async def test_all_none_5d_falls_back(self):
        fl = _learner(FakeDB())
        stats = await fl._generate_stats([
            {"return_1d": 2.0, "return_5d": None, "is_correct": True},
        ])
        assert abs(stats["avg_return_5d"] - 2.0) < 1e-9, "전부 미성숙이면 1d로 대체"
