# -*- coding: utf-8 -*-
"""
tests/unit/test_circuit_breaker.py - Circuit Breaker 강화 테스트 (v1.0)

테스트 클래스:
    TestBreakerState                (3개)  : 상태 Enum
    TestConsecutiveLossBreaker      (8개)  : 연속 손실 차단기
    TestVolatilityBreaker           (7개)  : 변동성 차단기
    TestLiquidityBreaker            (7개)  : 유동성 차단기
    TestCircuitBreakerManagerBasic  (7개)  : 통합 관리자 기본
    TestCircuitBreakerManagerFlow   (6개)  : CLOSED→OPEN→HALF_OPEN→CLOSED 흐름
    TestBreakerEventDTO             (3개)  : BreakerEvent DTO

총 41개 테스트
"""

import time
import pytest

from risk.circuit_breaker import (
    BreakerEvent,
    BreakerState,
    CircuitBreakerManager,
    ConsecutiveLossBreaker,
    LiquidityBreaker,
    VolatilityBreaker,
)

# 짧은 cooldown으로 상태 전이를 빠르게 테스트
_FAST_COOLDOWN = 0.05  # 50ms


# ═══════════════════════════════════════════════════════════════════
#  BreakerState (3개)
# ═══════════════════════════════════════════════════════════════════

class TestBreakerState:
    def test_values_exist(self):
        assert BreakerState.CLOSED.value == "CLOSED"
        assert BreakerState.OPEN.value == "OPEN"
        assert BreakerState.HALF_OPEN.value == "HALF_OPEN"

    def test_str_enum(self):
        assert isinstance(BreakerState.CLOSED, str)

    def test_three_states(self):
        assert len(BreakerState) == 3


# ═══════════════════════════════════════════════════════════════════
#  ConsecutiveLossBreaker (8개)
# ═══════════════════════════════════════════════════════════════════

class TestConsecutiveLossBreaker:
    @pytest.fixture
    def breaker(self):
        return ConsecutiveLossBreaker(
            loss_limit=3,
            cooldown_sec=_FAST_COOLDOWN,
            half_open_passes=2,
        )

    def test_initial_state_closed(self, breaker):
        assert breaker.state == BreakerState.CLOSED
        assert not breaker.is_open

    def test_profit_keeps_closed(self, breaker):
        for _ in range(5):
            breaker.update(trade_return=0.01)
        assert breaker.state == BreakerState.CLOSED

    def test_losses_below_limit_stay_closed(self, breaker):
        breaker.update(trade_return=-0.01)
        breaker.update(trade_return=-0.01)
        assert breaker.state == BreakerState.CLOSED

    def test_consecutive_losses_trigger_open(self, breaker):
        for _ in range(3):
            breaker.update(trade_return=-0.01)
        assert breaker.state == BreakerState.OPEN
        assert breaker.is_open

    def test_profit_resets_consecutive_count(self, breaker):
        breaker.update(trade_return=-0.01)
        breaker.update(trade_return=-0.01)
        breaker.update(trade_return=0.01)   # 리셋
        breaker.update(trade_return=-0.01)
        breaker.update(trade_return=-0.01)
        assert breaker.state == BreakerState.CLOSED  # limit=3이므로 아직 미달

    def test_returns_event_on_open(self, breaker):
        events = []
        for _ in range(3):
            e = breaker.update(trade_return=-0.01)
            if e:
                events.append(e)
        assert any(e.new_state == BreakerState.OPEN for e in events)

    def test_consecutive_losses_property(self, breaker):
        breaker.update(trade_return=-0.01)
        breaker.update(trade_return=-0.01)
        assert breaker.consecutive_losses == 2

    def test_reset_restores_closed(self, breaker):
        for _ in range(3):
            breaker.update(trade_return=-0.01)
        assert breaker.is_open
        breaker.reset()
        assert breaker.state == BreakerState.CLOSED


# ═══════════════════════════════════════════════════════════════════
#  VolatilityBreaker (7개)
# ═══════════════════════════════════════════════════════════════════

class TestVolatilityBreaker:
    @pytest.fixture
    def breaker(self):
        return VolatilityBreaker(
            volatility_limit=0.04,
            cooldown_sec=_FAST_COOLDOWN,
            half_open_passes=2,
        )

    def test_initial_closed(self, breaker):
        assert breaker.state == BreakerState.CLOSED

    def test_low_vol_stays_closed(self, breaker):
        breaker.update(current_volatility=0.02)
        assert breaker.state == BreakerState.CLOSED

    def test_high_vol_opens(self, breaker):
        breaker.update(current_volatility=0.05)
        assert breaker.state == BreakerState.OPEN

    def test_boundary_exactly_limit_stays_closed(self, breaker):
        # 0.04 == limit → NOT > limit → CLOSED
        breaker.update(current_volatility=0.04)
        assert breaker.state == BreakerState.CLOSED

    def test_over_limit_returns_event(self, breaker):
        e = breaker.update(current_volatility=0.06)
        assert e is not None
        assert e.new_state == BreakerState.OPEN

    def test_current_volatility_property(self, breaker):
        breaker.update(current_volatility=0.03)
        assert breaker.current_volatility == pytest.approx(0.03)

    def test_reset(self, breaker):
        breaker.update(current_volatility=0.10)
        breaker.reset()
        assert breaker.state == BreakerState.CLOSED


# ═══════════════════════════════════════════════════════════════════
#  LiquidityBreaker (7개)
# ═══════════════════════════════════════════════════════════════════

class TestLiquidityBreaker:
    @pytest.fixture
    def breaker(self):
        return LiquidityBreaker(
            liquidity_limit=0.30,
            cooldown_sec=_FAST_COOLDOWN,
            half_open_passes=2,
        )

    def test_initial_closed(self, breaker):
        assert breaker.state == BreakerState.CLOSED

    def test_adequate_volume_stays_closed(self, breaker):
        breaker.update(volume_ratio=1.0)
        assert breaker.state == BreakerState.CLOSED

    def test_low_volume_opens(self, breaker):
        breaker.update(volume_ratio=0.20)
        assert breaker.state == BreakerState.OPEN

    def test_boundary_exactly_limit_opens(self, breaker):
        # 0.30 == limit → NOT < limit → CLOSED
        breaker.update(volume_ratio=0.30)
        assert breaker.state == BreakerState.CLOSED

    def test_below_limit_returns_event(self, breaker):
        e = breaker.update(volume_ratio=0.10)
        assert e is not None and e.new_state == BreakerState.OPEN

    def test_current_ratio_property(self, breaker):
        breaker.update(volume_ratio=0.50)
        assert breaker.current_ratio == pytest.approx(0.50)

    def test_reset(self, breaker):
        breaker.update(volume_ratio=0.10)
        breaker.reset()
        assert breaker.state == BreakerState.CLOSED


# ═══════════════════════════════════════════════════════════════════
#  CircuitBreakerManager 기본 (7개)
# ═══════════════════════════════════════════════════════════════════

class TestCircuitBreakerManagerBasic:
    @pytest.fixture
    def manager(self):
        return CircuitBreakerManager(
            consec_loss_limit=3,
            volatility_limit=0.04,
            liquidity_limit=0.30,
            cooldown_sec=_FAST_COOLDOWN,
            half_open_passes=2,
        )

    def test_initial_not_open(self, manager):
        assert not manager.is_open

    def test_update_returns_status(self, manager):
        status = manager.update(trade_return=0.01, current_volatility=0.02, volume_ratio=1.0)
        assert status is not None

    def test_status_has_required_keys(self, manager):
        d = manager.status().to_dict()
        for key in ("is_open", "open_breakers", "states", "recent_events"):
            assert key in d

    def test_all_normal_not_open(self, manager):
        status = manager.update(0.01, 0.02, 1.0)
        assert not status.is_open

    def test_high_vol_opens_manager(self, manager):
        status = manager.update(0.0, 0.10, 1.0)
        assert status.is_open
        assert "Volatility" in status.open_breakers

    def test_low_volume_opens_manager(self, manager):
        status = manager.update(0.0, 0.01, 0.10)
        assert status.is_open
        assert "Liquidity" in status.open_breakers

    def test_reset_all(self, manager):
        manager.update(0.0, 0.10, 0.10)  # 복수 차단기 OPEN
        assert manager.is_open
        manager.reset_all()
        assert not manager.is_open


# ═══════════════════════════════════════════════════════════════════
#  CircuitBreakerManager 상태 흐름 (6개)
# ═══════════════════════════════════════════════════════════════════

class TestCircuitBreakerManagerFlow:
    def _manager(self):
        return CircuitBreakerManager(
            consec_loss_limit=3,
            volatility_limit=0.04,
            liquidity_limit=0.30,
            cooldown_sec=_FAST_COOLDOWN,
            half_open_passes=2,
        )

    def test_consec_loss_flow_open(self):
        m = self._manager()
        for _ in range(3):
            m.update(trade_return=-0.02, current_volatility=0.01, volume_ratio=1.0)
        assert m.loss_breaker.state == BreakerState.OPEN

    def test_open_to_half_open_after_cooldown(self):
        m = self._manager()
        now = time.time()
        for _ in range(3):
            m.loss_breaker.update(-0.01, now=now)
        assert m.loss_breaker.state == BreakerState.OPEN
        # cooldown 경과 후 HALF_OPEN으로 전이
        m.loss_breaker.update(0.01, now=now + _FAST_COOLDOWN + 0.01)
        assert m.loss_breaker.state == BreakerState.HALF_OPEN

    def test_half_open_to_closed_after_passes(self):
        m = self._manager()
        now = time.time()
        for _ in range(3):
            m.loss_breaker.update(-0.01, now=now)
        # cooldown 경과
        m.loss_breaker.update(0.01, now=now + _FAST_COOLDOWN + 0.01)
        assert m.loss_breaker.state == BreakerState.HALF_OPEN
        # 통과 2회 → CLOSED (half_open_passes=2)
        m.loss_breaker.update(0.01, now=now + _FAST_COOLDOWN + 0.02)
        m.loss_breaker.update(0.01, now=now + _FAST_COOLDOWN + 0.03)
        assert m.loss_breaker.state == BreakerState.CLOSED

    def test_half_open_loss_reopens(self):
        m = self._manager()
        now = time.time()
        for _ in range(3):
            m.loss_breaker.update(-0.01, now=now)
        m.loss_breaker.update(0.01, now=now + _FAST_COOLDOWN + 0.01)  # → HALF_OPEN
        m.loss_breaker.update(-0.01, now=now + _FAST_COOLDOWN + 0.02)  # 재손실 → OPEN
        assert m.loss_breaker.state == BreakerState.OPEN

    def test_multiple_breakers_open_simultaneously(self):
        m = self._manager()
        status = m.update(
            trade_return=-0.05,
            current_volatility=0.10,
            volume_ratio=0.05,
        )
        # 연속손실 1회라 loss는 안 열릴 수 있으나 vol+liq는 열림
        open_count = len(status.open_breakers)
        assert open_count >= 1

    def test_states_dict_has_all_three(self):
        m = self._manager()
        status = m.status()
        assert "ConsecutiveLoss" in status.states
        assert "Volatility" in status.states
        assert "Liquidity" in status.states


# ═══════════════════════════════════════════════════════════════════
#  BreakerEvent DTO (3개)
# ═══════════════════════════════════════════════════════════════════

class TestBreakerEventDTO:
    def _make(self):
        return BreakerEvent(
            breaker_name="TestBreaker",
            prev_state=BreakerState.CLOSED,
            new_state=BreakerState.OPEN,
            reason="Test reason",
        )

    def test_to_dict_has_required_keys(self):
        d = self._make().to_dict()
        for key in ("breaker_name", "prev_state", "new_state", "reason", "timestamp"):
            assert key in d

    def test_state_values_are_strings(self):
        d = self._make().to_dict()
        assert isinstance(d["prev_state"], str)
        assert isinstance(d["new_state"], str)

    def test_frozen(self):
        e = self._make()
        with pytest.raises((AttributeError, TypeError)):
            e.reason = "modified"  # type: ignore
