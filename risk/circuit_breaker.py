# -*- coding: utf-8 -*-
"""
risk/circuit_breaker.py - Circuit Breaker 강화 v1.0

개요:
    3가지 회로 차단 조건을 독립적으로 모니터링한다:
        1. 연속 손실 (ConsecutiveLossBreaker)
        2. 변동성 급등 (VolatilityBreaker)
        3. 유동성 위기 — 거래량 급감 (LiquidityBreaker)

    CircuitBreakerManager가 세 차단기를 통합 관리하며,
    어느 하나라도 OPEN 상태이면 전체 거래를 중단한다.

상태 머신:
    CLOSED → (조건 충족) → OPEN → (cooldown 경과) → HALF_OPEN
    HALF_OPEN → (정상 신호 수신) → CLOSED
    HALF_OPEN → (조건 재충족) → OPEN

사용 방법:
    manager = CircuitBreakerManager()
    state = manager.update(
        trade_return=-0.03, current_volatility=0.05,
        volume_ratio=0.8, consecutive_losses=4,
    )
    if manager.is_open:
        # 주문 차단
        ...
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("circuit_breaker")


# ─── 상태 ────────────────────────────────────────────────────────

class BreakerState(str, Enum):
    CLOSED    = "CLOSED"     # 정상: 거래 허용
    OPEN      = "OPEN"       # 차단: 거래 금지
    HALF_OPEN = "HALF_OPEN"  # 복구 시험: 제한적 허용


# ─── 기본 임계값 ─────────────────────────────────────────────────
_DEFAULT_CONSEC_LOSS_LIMIT = 5          # 연속 손실 허용 횟수
_DEFAULT_VOLATILITY_LIMIT  = 0.04       # 일간 변동성 4% 초과 → OPEN
_DEFAULT_LIQUIDITY_LIMIT   = 0.30       # 거래량비율 30% 미만 → OPEN
_DEFAULT_COOLDOWN_SEC      = 300        # OPEN→HALF_OPEN 대기 (5분)
_DEFAULT_HALF_OPEN_PASSES  = 3          # HALF_OPEN에서 통과 필요 횟수


# ═══════════════════════════════════════════════════════════════════
#  DTO
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BreakerEvent:
    """회로 차단/복구 이벤트.

    Attributes:
        breaker_name: 차단기 이름
        prev_state: 이전 상태
        new_state: 새 상태
        reason: 전이 사유
        timestamp: 이벤트 발생 시각
    """
    breaker_name: str
    prev_state: BreakerState
    new_state: BreakerState
    reason: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "breaker_name": self.breaker_name,
            "prev_state": self.prev_state.value,
            "new_state": self.new_state.value,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


# ═══════════════════════════════════════════════════════════════════
#  개별 차단기
# ═══════════════════════════════════════════════════════════════════

class _BaseBreaker:
    """차단기 공통 기반 클래스."""

    def __init__(
        self,
        name: str,
        cooldown_sec: float = _DEFAULT_COOLDOWN_SEC,
        half_open_passes: int = _DEFAULT_HALF_OPEN_PASSES,
    ) -> None:
        self.name = name
        self._cooldown_sec = cooldown_sec
        self._half_open_passes = half_open_passes
        self._state = BreakerState.CLOSED
        self._open_at: Optional[float] = None
        self._half_open_pass_count = 0
        self._events: List[BreakerEvent] = []

    @property
    def state(self) -> BreakerState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state == BreakerState.OPEN

    def _transition(self, new_state: BreakerState, reason: str) -> BreakerEvent:
        event = BreakerEvent(
            breaker_name=self.name,
            prev_state=self._state,
            new_state=new_state,
            reason=reason,
        )
        self._events.append(event)
        if len(self._events) > 200:
            self._events.pop(0)
        prev = self._state
        self._state = new_state
        logger.warning(
            "[CircuitBreaker] %s: %s → %s | %s",
            self.name, prev.value, new_state.value, reason,
        )
        return event

    def _try_recover(self, now: float) -> Optional[BreakerEvent]:
        """OPEN 상태에서 cooldown이 지나면 HALF_OPEN으로 전이."""
        if self._state == BreakerState.OPEN and self._open_at is not None:
            if now - self._open_at >= self._cooldown_sec:
                self._half_open_pass_count = 0
                return self._transition(
                    BreakerState.HALF_OPEN,
                    f"Cooldown {self._cooldown_sec:.0f}s elapsed",
                )
        return None

    def _open(self, reason: str, now: float) -> BreakerEvent:
        self._open_at = now
        self._half_open_pass_count = 0
        return self._transition(BreakerState.OPEN, reason)

    def _pass_half_open(self) -> Optional[BreakerEvent]:
        """HALF_OPEN 상태에서 통과 횟수 누적 → CLOSED 복구."""
        self._half_open_pass_count += 1
        if self._half_open_pass_count >= self._half_open_passes:
            return self._transition(
                BreakerState.CLOSED,
                f"Recovered after {self._half_open_passes} passes",
            )
        return None

    @property
    def recent_events(self) -> List[BreakerEvent]:
        return list(self._events[-10:])

    def reset(self) -> None:
        """강제 초기화 (테스트/관리용)."""
        self._state = BreakerState.CLOSED
        self._open_at = None
        self._half_open_pass_count = 0


class ConsecutiveLossBreaker(_BaseBreaker):
    """연속 손실 차단기.

    Args:
        loss_limit: 연속 손실 허용 횟수 (기본 5)
        cooldown_sec: OPEN 후 HALF_OPEN 대기 시간
        half_open_passes: HALF_OPEN → CLOSED 통과 필요 횟수
    """

    def __init__(
        self,
        loss_limit: int = _DEFAULT_CONSEC_LOSS_LIMIT,
        cooldown_sec: float = _DEFAULT_COOLDOWN_SEC,
        half_open_passes: int = _DEFAULT_HALF_OPEN_PASSES,
    ) -> None:
        super().__init__("ConsecutiveLoss", cooldown_sec, half_open_passes)
        self._loss_limit = loss_limit
        self._consecutive = 0

    def update(
        self,
        trade_return: float,
        now: Optional[float] = None,
    ) -> Optional[BreakerEvent]:
        """트레이드 수익률을 업데이트하고 상태 변화가 있으면 BreakerEvent 반환."""
        now = now or time.time()

        # OPEN → HALF_OPEN 복구 시도
        event = self._try_recover(now)
        if event:
            return event

        if trade_return < 0:
            self._consecutive += 1
        else:
            self._consecutive = 0

        if self._state == BreakerState.CLOSED:
            if self._consecutive >= self._loss_limit:
                return self._open(
                    f"Consecutive losses: {self._consecutive}/{self._loss_limit}",
                    now,
                )

        elif self._state == BreakerState.HALF_OPEN:
            if trade_return < 0:
                return self._open(
                    f"Loss in HALF_OPEN (return={trade_return:.3f})",
                    now,
                )
            return self._pass_half_open()

        return None

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive


class VolatilityBreaker(_BaseBreaker):
    """변동성 급등 차단기.

    Args:
        volatility_limit: 일간 변동성 임계값 (기본 0.04 = 4%)
        cooldown_sec: OPEN 후 복구 대기 시간
    """

    def __init__(
        self,
        volatility_limit: float = _DEFAULT_VOLATILITY_LIMIT,
        cooldown_sec: float = _DEFAULT_COOLDOWN_SEC,
        half_open_passes: int = _DEFAULT_HALF_OPEN_PASSES,
    ) -> None:
        super().__init__("Volatility", cooldown_sec, half_open_passes)
        self._volatility_limit = volatility_limit
        self._current_volatility: float = 0.0

    def update(
        self,
        current_volatility: float,
        now: Optional[float] = None,
    ) -> Optional[BreakerEvent]:
        """변동성 값을 업데이트하고 상태 변화가 있으면 BreakerEvent 반환."""
        now = now or time.time()
        self._current_volatility = current_volatility

        event = self._try_recover(now)
        if event:
            return event

        if self._state == BreakerState.CLOSED:
            if current_volatility > self._volatility_limit:
                return self._open(
                    f"Volatility {current_volatility:.4f} > limit {self._volatility_limit:.4f}",
                    now,
                )

        elif self._state == BreakerState.HALF_OPEN:
            if current_volatility > self._volatility_limit:
                return self._open(
                    f"Volatility still high in HALF_OPEN ({current_volatility:.4f})",
                    now,
                )
            return self._pass_half_open()

        return None

    @property
    def current_volatility(self) -> float:
        return self._current_volatility


class LiquidityBreaker(_BaseBreaker):
    """유동성 위기 차단기.

    volume_ratio = 현재거래량 / 20일평균거래량.
    이 비율이 낮으면 유동성 부족 → 거래 중단.

    Args:
        liquidity_limit: volume_ratio 최소 임계값 (기본 0.30 = 30%)
        cooldown_sec: OPEN 후 복구 대기 시간
    """

    def __init__(
        self,
        liquidity_limit: float = _DEFAULT_LIQUIDITY_LIMIT,
        cooldown_sec: float = _DEFAULT_COOLDOWN_SEC,
        half_open_passes: int = _DEFAULT_HALF_OPEN_PASSES,
    ) -> None:
        super().__init__("Liquidity", cooldown_sec, half_open_passes)
        self._liquidity_limit = liquidity_limit
        self._current_ratio: float = 1.0

    def update(
        self,
        volume_ratio: float,
        now: Optional[float] = None,
    ) -> Optional[BreakerEvent]:
        """거래량 비율을 업데이트하고 상태 변화가 있으면 BreakerEvent 반환."""
        now = now or time.time()
        self._current_ratio = volume_ratio

        event = self._try_recover(now)
        if event:
            return event

        if self._state == BreakerState.CLOSED:
            if volume_ratio < self._liquidity_limit:
                return self._open(
                    f"Volume ratio {volume_ratio:.3f} < limit {self._liquidity_limit:.3f}",
                    now,
                )

        elif self._state == BreakerState.HALF_OPEN:
            if volume_ratio < self._liquidity_limit:
                return self._open(
                    f"Volume still low in HALF_OPEN ({volume_ratio:.3f})",
                    now,
                )
            return self._pass_half_open()

        return None

    @property
    def current_ratio(self) -> float:
        return self._current_ratio


# ═══════════════════════════════════════════════════════════════════
#  CircuitBreakerManager
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CircuitBreakerStatus:
    """전체 회로 차단기 상태 요약."""
    is_open: bool
    open_breakers: List[str]
    states: Dict[str, str]
    recent_events: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_open": self.is_open,
            "open_breakers": self.open_breakers,
            "states": self.states,
            "recent_events": self.recent_events,
        }


class CircuitBreakerManager:
    """3개 차단기 통합 관리자.

    Args:
        consec_loss_limit: 연속 손실 허용 횟수
        volatility_limit: 변동성 임계값
        liquidity_limit: 거래량 비율 임계값
        cooldown_sec: 모든 차단기의 공통 cooldown (초)
        half_open_passes: HALF_OPEN 복구 통과 횟수
    """

    def __init__(
        self,
        consec_loss_limit: int = _DEFAULT_CONSEC_LOSS_LIMIT,
        volatility_limit: float = _DEFAULT_VOLATILITY_LIMIT,
        liquidity_limit: float = _DEFAULT_LIQUIDITY_LIMIT,
        cooldown_sec: float = _DEFAULT_COOLDOWN_SEC,
        half_open_passes: int = _DEFAULT_HALF_OPEN_PASSES,
    ) -> None:
        self._loss_breaker = ConsecutiveLossBreaker(
            consec_loss_limit, cooldown_sec, half_open_passes
        )
        self._vol_breaker = VolatilityBreaker(
            volatility_limit, cooldown_sec, half_open_passes
        )
        self._liq_breaker = LiquidityBreaker(
            liquidity_limit, cooldown_sec, half_open_passes
        )
        self._all_events: List[BreakerEvent] = []

    def update(
        self,
        trade_return: float = 0.0,
        current_volatility: float = 0.0,
        volume_ratio: float = 1.0,
        now: Optional[float] = None,
    ) -> CircuitBreakerStatus:
        """세 차단기를 한 번에 업데이트하고 통합 상태를 반환합니다.

        Args:
            trade_return: 가장 최근 트레이드 수익률 (예: -0.02 = -2%)
            current_volatility: 현재 일간 변동성 (예: 0.05 = 5%)
            volume_ratio: 현재 거래량 / 20일 평균 거래량
            now: 현재 시각 (None이면 time.time())

        Returns:
            CircuitBreakerStatus: 통합 상태
        """
        now = now or time.time()
        events = []

        e = self._loss_breaker.update(trade_return, now)
        if e:
            events.append(e)
        e = self._vol_breaker.update(current_volatility, now)
        if e:
            events.append(e)
        e = self._liq_breaker.update(volume_ratio, now)
        if e:
            events.append(e)

        self._all_events.extend(events)
        if len(self._all_events) > 500:
            self._all_events = self._all_events[-500:]

        return self.status()

    def status(self) -> CircuitBreakerStatus:
        """현재 통합 상태 반환 (update 없이 조회만 할 때)."""
        breakers = {
            "ConsecutiveLoss": self._loss_breaker,
            "Volatility": self._vol_breaker,
            "Liquidity": self._liq_breaker,
        }
        open_list = [name for name, b in breakers.items() if b.is_open]
        states = {name: b.state.value for name, b in breakers.items()}
        recent = [e.to_dict() for e in self._all_events[-5:]]

        return CircuitBreakerStatus(
            is_open=len(open_list) > 0,
            open_breakers=open_list,
            states=states,
            recent_events=recent,
        )

    @property
    def is_open(self) -> bool:
        """어느 차단기든 OPEN이면 True."""
        return (
            self._loss_breaker.is_open
            or self._vol_breaker.is_open
            or self._liq_breaker.is_open
        )

    def reset_all(self) -> None:
        """모든 차단기를 CLOSED로 강제 초기화."""
        self._loss_breaker.reset()
        self._vol_breaker.reset()
        self._liq_breaker.reset()
        logger.info("[CircuitBreakerManager] All breakers reset to CLOSED")

    @property
    def loss_breaker(self) -> ConsecutiveLossBreaker:
        return self._loss_breaker

    @property
    def vol_breaker(self) -> VolatilityBreaker:
        return self._vol_breaker

    @property
    def liq_breaker(self) -> LiquidityBreaker:
        return self._liq_breaker
