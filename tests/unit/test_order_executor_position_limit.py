# -*- coding: utf-8 -*-
"""
tests/unit/test_order_executor_position_limit.py

OrderExecutor v2.0 PortfolioVaR position_limit 연동 테스트
- update_position_limit() / get_position_limit()
- _position_size_check() position_limit 기반 수량 검증

telegram mock은 tests/conftest.py에서 전역 설치됨.
"""

import asyncio
import pytest
from unittest.mock import MagicMock

from execution.order_executor import OrderExecutor, OrderMode, OrderRequest


# ─────────────────────────────────────────────────────────────────────────────
#  헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def make_executor(position_limit: float = 1.0) -> OrderExecutor:
    executor = OrderExecutor(
        kiwoom_connector=MagicMock(),
        db_manager=MagicMock(),
        telegram_sender=MagicMock(),
        mode=OrderMode.PAPER,
    )
    if position_limit != 1.0:
        executor.update_position_limit(position_limit)
    return executor


def make_request(qty: int = 500, action: str = "BUY") -> OrderRequest:
    return OrderRequest(ticker="AAPL", action=action, quantity=qty, price=100.0)


def run(coro):
    """새 이벤트 루프를 생성하여 코루틴 실행 (기존 루프 상태 영향 없음)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────────
#  TestUpdatePositionLimit
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdatePositionLimit:
    def test_default_is_one(self):
        assert make_executor().get_position_limit() == 1.0

    def test_update_valid(self):
        ex = make_executor()
        ex.update_position_limit(0.75)
        assert ex.get_position_limit() == 0.75

    def test_update_lower_bound(self):
        ex = make_executor()
        ex.update_position_limit(0.01)
        assert ex.get_position_limit() == 0.01

    def test_update_upper_bound(self):
        ex = make_executor()
        ex.update_position_limit(1.0)
        assert ex.get_position_limit() == 1.0

    def test_zero_rejected(self):
        ex = make_executor()
        ex.update_position_limit(0.0)
        assert ex.get_position_limit() == 1.0

    def test_negative_rejected(self):
        ex = make_executor()
        ex.update_position_limit(-0.5)
        assert ex.get_position_limit() == 1.0

    def test_over_one_rejected(self):
        ex = make_executor()
        ex.update_position_limit(1.5)
        assert ex.get_position_limit() == 1.0

    def test_sequence(self):
        ex = make_executor()
        for v in [0.90, 0.75, 0.50]:
            ex.update_position_limit(v)
        assert ex.get_position_limit() == 0.50


# ─────────────────────────────────────────────────────────────────────────────
#  TestPositionSizeCheck
# ─────────────────────────────────────────────────────────────────────────────

class TestPositionSizeCheck:
    def test_default_1000_passes(self):
        passed, _ = run(make_executor()._position_size_check(make_request(1000)))
        assert passed is True

    def test_default_1001_fails(self):
        passed, reason = run(make_executor()._position_size_check(make_request(1001)))
        assert not passed
        assert "1001" in reason

    def test_limit_075_max_750_ok(self):
        ex = make_executor(0.75)
        passed, _ = run(ex._position_size_check(make_request(750)))
        assert passed is True

    def test_limit_075_max_750_fail(self):
        ex = make_executor(0.75)
        passed, _ = run(ex._position_size_check(make_request(751)))
        assert not passed

    def test_limit_05_max_500_ok(self):
        ex = make_executor(0.50)
        passed, _ = run(ex._position_size_check(make_request(500)))
        assert passed is True

    def test_limit_05_max_500_fail(self):
        ex = make_executor(0.50)
        passed, _ = run(ex._position_size_check(make_request(501)))
        assert not passed

    def test_limit_01_max_100(self):
        ex = make_executor(0.10)
        assert run(ex._position_size_check(make_request(100)))[0] is True
        assert run(ex._position_size_check(make_request(101)))[0] is False

    def test_reason_contains_limit_info(self):
        ex = make_executor(0.75)
        _, reason = run(ex._position_size_check(make_request(800)))
        assert "0.75" in reason or "position_limit" in reason.lower()

    def test_zero_qty_passes(self):
        ex = make_executor(0.50)
        passed, _ = run(ex._position_size_check(make_request(0)))
        assert passed is True


# ─────────────────────────────────────────────────────────────────────────────
#  TestPositionLimitIntegration
# ─────────────────────────────────────────────────────────────────────────────

class TestPositionLimitIntegration:
    def test_dynamic_reduce(self):
        ex = make_executor()
        assert run(ex._position_size_check(make_request(1000)))[0] is True

        ex.update_position_limit(0.50)
        assert run(ex._position_size_check(make_request(1000)))[0] is False
        assert run(ex._position_size_check(make_request(500)))[0] is True

    def test_restore_limit(self):
        ex = make_executor(0.50)
        assert run(ex._position_size_check(make_request(501)))[0] is False

        ex.update_position_limit(1.0)
        assert run(ex._position_size_check(make_request(1000)))[0] is True

    def test_var_tiers(self):
        """VaR 구간별 position_limit 티어 전환."""
        ex = make_executor()
        scenarios = [
            (1.0,  1000, True),
            (0.90,  900, True),
            (0.90,  901, False),
            (0.75,  750, True),
            (0.75,  751, False),
            (0.50,  500, True),
            (0.50,  501, False),
        ]
        for limit, qty, expected in scenarios:
            ex.update_position_limit(limit)
            passed, _ = run(ex._position_size_check(make_request(qty)))
            assert passed is expected, f"limit={limit}, qty={qty}"
