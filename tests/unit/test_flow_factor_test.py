"""수급 팩터 검증 도구 테스트 (2026-10-19).

핵심 교훈을 회귀 테스트로 고정한다:
- 연속 지속일 중복은 t값을 과대평가한다(1차 검정 착시) → dedupe_runs 기본 True.
- 초과수익 = 개별 전방수익 − 같은 날 유니버스 평균.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from validation.flow_factor_test import (  # noqa: E402
    Series,
    _back_return,
    _fwd_return,
    _streak_buy,
    _tstat,
    collect_events,
    summarize,
    universe_means,
)


class TestMath:

    def test_fwd_return(self):
        closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 110.0]
        assert abs(_fwd_return(closes, 0, 5) - 0.05) < 1e-9
        assert _fwd_return(closes, 5, 5) is None  # 미래 데이터 없음
        assert _fwd_return([0.0, 1.0, 1.0, 1.0, 1.0, 1.0], 0, 5) is None  # 시작가 0가드

    def test_back_return(self):
        closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
        assert abs(_back_return(closes, 5, 5) - 0.05) < 1e-9
        assert _back_return(closes, 2, 5) is None

    def test_streak(self):
        assert _streak_buy([1, 1, 1], 2, 3) is True
        assert _streak_buy([1, -1, 1], 2, 3) is False
        assert _streak_buy([1, 1], 1, 3) is False  # 길이 부족

    def test_tstat(self):
        assert _tstat([1.0, 1.0, 1.0]) == 0.0  # 분산 0
        assert _tstat([1.0]) == 0.0
        t = _tstat([1.0, 2.0, 3.0, 4.0, 5.0])
        assert t > 3.0


def _mk_series(dates, closes, f, i):
    return Series(dates=list(dates), close=list(closes), volume=[1.0] * len(closes),
                  foreign=list(f), inst=list(i))


class TestDedupe:

    def _two_day_run(self):
        # 16일: d=5에서 처음 f5>0(전일 불성립) → 이후 유지. 전방 5일 데이터 확보.
        dates = [f"2026-01-{d:02d}" for d in range(1, 17)]
        closes = [100.0] * 16
        f = [-1, -1, -1, -1, -1] + [5] * 11   # d=5: f5=sum(f[1..5])=-4+5=+1 >0 (첫 성립)
        i = [0] * 16
        return {"A": _mk_series(dates, closes, f, i)}

    def test_dedupe_counts_once(self):
        events = collect_events(self._two_day_run(), dedupe_runs=True)
        assert len(events["D_외국인매수"]) == 1, "켜지는 날만 기록되어야 함"
        assert events["D_외국인매수"][0]["date"] == "2026-01-06"

    def test_no_dedupe_counts_each_day(self):
        events = collect_events(self._two_day_run(), dedupe_runs=False)
        assert len(events["D_외국인매수"]) > 1, "미제거 시 연속일 다수"""


class TestSummarize:

    def test_excess_vector(self):
        # 2종목 × 2일: 같은 날 평균 대비 초과 계산 검증
        events = [
            {"date": "2026-01-01", "fr5": 0.02},
            {"date": "2026-01-02", "fr5": -0.01},
        ]
        means = {"2026-01-01": 0.00, "2026-01-02": 0.00}
        st = summarize(events, means, 5)
        assert st["n"] == 2
        assert abs(st["mean"] - 0.5) < 1e-6  # (+2% -1%)/2 = +0.5%p
        assert st["hit"] == 50.0

    def test_yearly_consistency(self):
        events = [
            {"date": "2022-06-01", "fr5": 0.01},
            {"date": "2023-06-01", "fr5": 0.01},
            {"date": "2024-06-01", "fr5": -0.01},
        ]
        st = summarize(events, {e["date"]: 0.0 for e in events}, 5)
        assert st["pos_years"] == 2 and st["n_years"] == 3

    def test_universe_means(self):
        s1 = _mk_series(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04",
                         "2026-01-05", "2026-01-06"],
                        [100, 100, 100, 100, 100, 110], [0] * 6, [0] * 6)
        s2 = _mk_series(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04",
                         "2026-01-05", "2026-01-06"],
                        [100, 100, 100, 100, 100, 100], [0] * 6, [0] * 6)
        m = universe_means({"s1": s1, "s2": s2}, 5)
        # 2026-01-01의 5일 전방: s1 +10%, s2 0% → 평균 +5%
        assert abs(m["2026-01-01"] - 0.05) < 1e-9


class TestRealScanWireReminder:
    """가드: 1차 검정(중복 미제거)의 t=3.94 착시를 문서화."""

    def test_default_is_deduped(self):
        import inspect

        from validation import flow_factor_test as ft

        sig = inspect.signature(ft.collect_events)
        assert sig.parameters["dedupe_runs"].default is True
