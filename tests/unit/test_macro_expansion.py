# -*- coding: utf-8 -*-
"""tests/unit/test_macro_expansion.py - 거시 지표 확장 검증 (P6-2)."""

from typing import Any, List, Tuple

import pytest

from filters.macro_filter import MacroFilter
from scheduler import macro_collector as mc


class TestMacroDataFields:
    def test_new_fields_present(self) -> None:
        d = mc.MacroData()

        for field in ("n225_trend", "dxy", "us_2y", "yield_spread", "copper_price", "gold_price"):
            assert hasattr(d, field), f"{field} 누락"

    def test_to_dict_includes_new_fields(self) -> None:
        d = mc.MacroData()

        for key in ("n225_trend", "dxy", "us_2y", "yield_spread", "copper_price", "gold_price"):
            assert key in d.to_dict(), f"{key} 누락"


class TestMacroFilterIndicators:
    def test_weights_sum_to_one(self) -> None:
        total = sum(s["weight"] for s in MacroFilter.INDICATORS.values())
        assert abs(total - 1.0) < 1e-6

    def test_every_indicator_has_std_estimate(self) -> None:
        missing = set(MacroFilter.INDICATORS) - set(MacroFilter.STD_ESTIMATES)
        assert missing == set()

    @pytest.mark.parametrize("key", ["n225_trend", "dxy", "yield_spread", "copper_price", "gold_price"])
    def test_new_indicator_scored(self, key: str) -> None:
        result = MacroFilter().check({})
        assert key in result["indicators"]
        assert 0.0 <= result["indicators"][key]["score"] <= 1.0

    def test_yield_inversion_scores_lower(self) -> None:
        """장단기 역전(음수)은 정상 커브보다 낮은 점수여야 한다."""
        normal = MacroFilter().check({"yield_spread": 1.5})
        inverted = MacroFilter().check({"yield_spread": -0.8})

        assert inverted["indicators"]["yield_spread"]["score"] < normal["indicators"]["yield_spread"]["score"]

    def test_copper_high_is_positive(self) -> None:
        boom = MacroFilter().check({"copper_price": 7.0})
        slump = MacroFilter().check({"copper_price": 3.0})

        assert boom["indicators"]["copper_price"]["score"] > slump["indicators"]["copper_price"]["score"]

    def test_gold_high_is_cautious(self) -> None:
        risk_off = MacroFilter().check({"gold_price": 4000.0})
        calm = MacroFilter().check({"gold_price": 1800.0})

        assert risk_off["indicators"]["gold_price"]["score"] < calm["indicators"]["gold_price"]["score"]

    def test_score_bounded(self) -> None:
        result = MacroFilter().check({})
        assert 0.0 <= result["score"] <= 1.0

    def test_macro_data_dump_included(self) -> None:
        result = MacroFilter().check({})
        assert "n225_trend" in result["macro_data"]


class TestKtbFallback:
    def test_falls_back_to_fred(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """네이버가 죽어도(410) FRED 폴백으로 값을 얻어야 한다 — 기본값 고정 방지."""
        def _boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("HTTP 410")

        monkeypatch.setattr(mc.requests, "get", _boom)
        monkeypatch.setattr(mc, "_fetch_fred", lambda sid: 4.286 if sid == "IRLTLT01KRM156N" else None)

        assert mc._fetch_ktb_yield() == pytest.approx(4.286)

    def test_returns_none_when_both_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("network down")

        monkeypatch.setattr(mc.requests, "get", _boom)
        monkeypatch.setattr(mc, "_fetch_fred", lambda sid: None)

        assert mc._fetch_ktb_yield() is None

    def test_prefers_naver_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Resp:
            text = '<td class="num">3.15</td>'
            status_code = 200

            def raise_for_status(self) -> None:
                return None

        monkeypatch.setattr(mc.requests, "get", lambda *a, **k: _Resp())
        monkeypatch.setattr(mc, "_fetch_fred", lambda sid: 9.99)

        assert mc._fetch_ktb_yield() == pytest.approx(3.15)


class TestKospiTickerFallback:
    async def test_uses_ks11_primary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """^KS200은 1행만 반환되어 추세 계산 불가 → ^KS11을 먼저 시도해야 한다."""
        calls: List[Tuple[str, str, bool]] = []

        def _fake(symbol: str, period: str = "5d", as_trend: bool = False) -> float | None:
            calls.append((symbol, period, as_trend))
            return 1.23 if symbol == "^KS11" else None

        monkeypatch.setattr(mc, "_fetch_yahoo", _fake)
        data = mc.MacroData()
        await _fetch_with(data, monkeypatch)

        assert calls[0][0] == "^KS11"
        assert data.kospi_trend == pytest.approx(1.23)

    async def test_kospi_zero_is_not_overwritten(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mc, "_fetch_yahoo", lambda *a, **k: None)
        data = mc.MacroData(kospi_trend=2.0)
        await _fetch_with(data, monkeypatch)

        assert data.kospi_trend == pytest.approx(2.0)


async def _fetch_with(data: mc.MacroData, monkeypatch: pytest.MonkeyPatch) -> None:
    """네트워크 없이 fetch_macro_data의 KOSPI 경로만 태운다."""
    monkeypatch.setattr(mc, "_cached_macro", data)
    monkeypatch.setattr(mc, "_last_fetch_time", 0.0)
    monkeypatch.setattr(mc, "_fetch_fred", lambda sid: None)
    monkeypatch.setattr(mc, "_fetch_ktb_yield", lambda: None)

    async def _noop(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(mc, "_send_alert", _noop)
    await mc.fetch_macro_data(force=True)
