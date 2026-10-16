"""기관 수급 추적 강화 테스트 (2026-10-16 Session 117 — 사용자 제안).

기관(연기금·외국인)은 분할 매매로 시장 충격을 줄이며, 그 흔적이 일별 수급에 남는다.
추적 지표(측정 전용 — 점수 불변):
- 연속성: 외국인/기관 3일+ 연속 순매수(매집) 또는 순매도(분산)
- 다이버전스: 가격↓ + 외국인 순매수 = 매집 의심 / 가격↑ + 순매도 = 분산 의심
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from application.analysis.nonprice_factors import NonPriceFactors, _flow_part  # noqa: E402


def _tags(f: NonPriceFactors):
    # build_tags와 동일 로직을 dataclass로 직접 호출
    import application.analysis.nonprice_factors as npf

    fn = None
    for name in ("build_tags", "_build_tags"):
        fn = getattr(npf, name, None)
        if fn:
            break
    assert fn, "build_tags 함수를 찾을 수 없음"
    return fn(f)


class _FakeDB:
    def __init__(self, sd_rows, closes):
        self._sd = sd_rows
        self._ol = [{"date": f"2026-10-{d:02d}", "close": c} for d, c in closes]

    async def get_supply_demand(self, ticker, days):
        return self._sd

    async def get_ohlcv(self, ticker, n):
        return self._ol


class TestFlowStreak:

    def test_foreign_buy_streak(self):
        rows = [
            {"date": "2026-10-10", "foreign_net": -100, "institution_net": 50},
            {"date": "2026-10-13", "foreign_net": 200, "institution_net": 60},
            {"date": "2026-10-14", "foreign_net": 300, "institution_net": 70},
            {"date": "2026-10-15", "foreign_net": 400, "institution_net": 80},
        ]
        db = _FakeDB(rows, [(10, 10000)])
        out = asyncio.run(_flow_part("005930", db))
        assert out["flow_foreign_streak"] == 3
        assert out["flow_inst_streak"] == 4

    def test_foreign_sell_streak(self):
        rows = [
            {"date": "2026-10-13", "foreign_net": -100, "institution_net": 5},
            {"date": "2026-10-14", "foreign_net": -200, "institution_net": 5},
            {"date": "2026-10-15", "foreign_net": -300, "institution_net": -5},
        ]
        db = _FakeDB(rows, [(10, 10000)])
        out = asyncio.run(_flow_part("005930", db))
        assert out["flow_foreign_streak"] == -3

    def test_zero_breaks_streak(self):
        rows = [
            {"date": "2026-10-14", "foreign_net": 100, "institution_net": 0},
            {"date": "2026-10-15", "foreign_net": 0, "institution_net": 10},
        ]
        db = _FakeDB(rows, [(10, 10000)])
        out = asyncio.run(_flow_part("005930", db))
        assert out["flow_foreign_streak"] == 0  # 최근일 0 → 연속 끊김
        assert out["flow_inst_streak"] == 1

    def test_price_5d(self):
        db = _FakeDB(
            [{"date": "2026-10-15", "foreign_net": 1, "institution_net": 1}],
            [(6, 10000), (7, 9800), (8, 9900), (9, 9700), (10, 9500)],
        )
        out = asyncio.run(_flow_part("005930", db))
        assert out["flow_price_5d"] == -5.0


class TestFlowTags:

    def test_streak_tags(self):
        f = NonPriceFactors("005930", flow_foreign_streak=3, flow_inst_streak=2)
        tags = _tags(f)
        assert "flow_f_streak3" in tags and "flow_both_streak" in tags

    def test_sell_streak_tag(self):
        f = NonPriceFactors("005930", flow_foreign_streak=-4)
        assert "flow_f_dnest3" in _tags(f)

    def test_divergence_bull(self):
        f = NonPriceFactors("005930", flow_price_5d=-3.5, flow_foreign_5d=5000.0)
        assert "flow_div_bull" in _tags(f)

    def test_divergence_bear(self):
        f = NonPriceFactors("005930", flow_price_5d=4.0, flow_foreign_5d=-5000.0)
        assert "flow_div_bear" in _tags(f)

    def test_no_divergence_when_aligned(self):
        f = NonPriceFactors("005930", flow_price_5d=4.0, flow_foreign_5d=5000.0)
        tags = _tags(f)
        assert "flow_div_bull" not in tags and "flow_div_bear" not in tags
