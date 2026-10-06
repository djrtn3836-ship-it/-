"""웹소켓 REAL 메시지 values(숫자 키) → 표준 필드 변환 회귀 테스트.

2026-10-06 실사고: 키움은 {"values": {"10": "+18630"}, "type": "0B"} 형태로 보내는데
변환 코드가 없어 2,321,000건 전부 "Invalid price: 0.0"으로 폐기됐다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data.kiwoom_connector import KiwoomConnectorV512, _to_float  # noqa: E402

REAL_0B = {
    "values": {
        "20": "181603", "10": "+18630", "11": "+830", "12": "+4.66",
        "27": "+18640", "28": "+18630", "15": "-20", "13": "13374614",
        "14": "247774", "16": "+18040", "17": "+18880", "18": "+17950",
        "1316": " 0", "9081": "KRX",
    },
    "type": "0B",
    "name": "주식체결",
    "item": "010170",
}


@pytest.fixture
def connector():
    return KiwoomConnectorV512.__new__(KiwoomConnectorV512)


class TestToFloat:
    def test_signed_and_spaced(self):
        assert _to_float("+18630") == 18630.0
        assert _to_float("-20") == -20.0
        assert _to_float(" 0") == 0.0
        assert _to_float("1,234") == 1234.0

    def test_invalid(self):
        assert _to_float(None) is None
        assert _to_float("abc") is None


class TestNormalize:
    def test_0b_real_message(self, connector):
        data = dict(REAL_0B)
        connector._normalize_ws_values(data)
        assert data["price"] == 18630.0
        assert data["change"] == 830.0
        assert data["change_rate"] == 4.66
        assert data["volume"] == 13374614
        assert data["open"] == 18040.0
        assert data["high"] == 18880.0
        assert data["low"] == 17950.0
        assert data["ask_price"] == 18640.0
        assert data["bid_price"] == 18630.0
        assert data["tick_time"] == "181603"

    def test_change_rate_consistency(self, connector):
        data = dict(REAL_0B)
        connector._normalize_ws_values(data)
        prev_close = data["price"] - data["change"]
        assert prev_close == 17800.0
        assert round(data["change"] / prev_close * 100, 2) == data["change_rate"]

    def test_market_tick_accepts(self, connector):
        from domain.models.market_tick import MarketTick
        import time
        data = dict(REAL_0B)
        connector._normalize_ws_values(data)
        tick = MarketTick(
            ticker=data["item"], price=data["price"],
            volume=data["volume"], timestamp=time.time(),
        )
        assert tick.price == 18630.0
        assert tick.volume == 13374614

    def test_unknown_type_untouched(self, connector):
        data = {"values": {"10": "+100"}, "type": "0Z", "item": "000000"}
        connector._normalize_ws_values(data)
        assert "price" not in data

    def test_no_values_untouched(self, connector):
        data = {"type": "0B", "item": "000000"}
        connector._normalize_ws_values(data)
        assert "price" not in data

    def test_existing_field_not_overwritten(self, connector):
        data = dict(REAL_0B)
        data["price"] = 999.0
        connector._normalize_ws_values(data)
        assert data["price"] == 999.0

    def test_invalid_number_skipped(self, connector):
        data = {"values": {"10": "N/A", "13": "N/A"}, "type": "0B", "item": "000000"}
        connector._normalize_ws_values(data)
        assert "price" not in data
        assert data["volume"] == 0
