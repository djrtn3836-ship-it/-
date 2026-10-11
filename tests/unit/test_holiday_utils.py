"""거래일 달력 테스트 (2026-10-10).

실버그(회귀 방지): `holidays.country_holidays("KR")` 무인자 인스턴스는
지연 확장이라 2026년만 확장돼 있었고, 그 결과 **2027년 공휴일이 통째로
누락**되어 신정(1/1)·삼일절(3/1)을 거래일로 오판했다
(is_trading_day("2027-01-01") → True). 연도별 조회+합집합으로 수정.
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.holiday_utils import (  # noqa: E402
    get_next_trading_day,
    is_market_open,
    is_trading_day,
)


class TestIsTradingDay:

    def test_weekend_is_not_trading(self):
        assert is_trading_day("2026-10-10") is False  # 토
        assert is_trading_day("2026-10-11") is False  # 일

    def test_known_holidays_2026(self):
        assert is_trading_day("2026-10-09") is False  # 한글날(실측 확인)
        assert is_trading_day("2026-12-25") is False  # 성탄절

    def test_weekday_is_trading(self):
        assert is_trading_day("2026-10-12") is True   # 월(다음 거래일)

    def test_2027_holidays_detected(self):
        """핵심 회귀: 연도 확장 누락으로 신정·삼일절이 거래일이 되면 안 된다."""
        pytest.importorskip("holidays")
        assert is_trading_day("2027-01-01") is False  # 신정
        assert is_trading_day("2027-03-01") is False  # 삼일절
        assert is_trading_day("2027-12-25") is False  # 성탄절
        assert is_trading_day("2027-01-04") is True   # 이듬해 평일

    def test_far_future_year_does_not_raise(self):
        assert isinstance(is_trading_day("2030-06-03"), bool)

    def test_datetime_and_date_inputs(self):
        assert is_trading_day(datetime(2026, 10, 12, 9, 0)) is True
        assert is_trading_day(date(2026, 10, 9)) is False

    def test_invalid_input(self):
        assert is_trading_day("not-a-date") is False


class TestNextTradingDay:

    def test_skips_holiday_and_weekend(self):
        # 2026-10-09(금, 한글날 휴장) → 다음 거래일은 10-12(월)
        nxt = get_next_trading_day(datetime(2026, 10, 9, 8, 0))
        assert nxt.date() == date(2026, 10, 12)

    def test_next_from_trading_day(self):
        # 연속 거래일: 10-12(월) → 10-13(화)
        nxt = get_next_trading_day(datetime(2026, 10, 12, 8, 0))
        assert nxt.date() == date(2026, 10, 13)


class TestMarketOpen:

    def test_returns_bool(self):
        assert isinstance(is_market_open(), bool)
