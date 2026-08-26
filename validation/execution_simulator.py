"""
validation/execution_simulator.py - v3.2 FINAL (asyncio.sleep() 버그 제거)
- Almgren-Chriss 파라미터를 한국 시장 데이터 기반으로 튜닝 (ALPHA=0.08, GAMMA=0.005)
- Fallback 시 시총 + 평균 거래량을 함께 고려하여 슬리피지 추정 정밀도 향상
- ExecutionResult에 remaining_volume 추가 (부분 체결 잔량 정보)
- 다중 호가 레벨 순회 + 부분 체결 정밀화 유지
- 🔥 v3.2: asyncio.sleep(0.3) 호출 제거 (동기 함수 내 await 없이 사용된 버그)
"""

import logging
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum

logger = logging.getLogger(__name__)


class MarketSession(Enum):
    PRE_OPEN = "pre_open"
    REGULAR = "regular"
    CLOSING_AUCTION = "closing_auction"
    AFTER_HOURS = "after_hours"
    CLOSED = "closed"


@dataclass
class ExecutionResult:
    filled: bool
    fill_ratio: float  # 0~1 (체결된 비율)
    execution_price: float  # 평균 체결가
    slippage_bps: float  # 전체 슬리피지 (bp)
    market_impact_bps: float  # 시장 충격에 의한 추가 슬리피지
    commission: float
    tax: float
    total_cost: float
    reason: str = ""
    slices: int = 0  # 분할 체결 슬라이스 수
    remaining_volume: int = 0  # 미체결 잔량 (부분 체결 시 활용)


class RealisticExecutionSimulator:
    SECURITIES_TAX = 0.0018  # 매도 시 증권거래세 (0.18%)
    BROKERAGE_FEE = 0.00015  # 수수료 (0.015%)

    # Almgren-Chriss 파라미터 (한국 시장 튜닝)
    ALPHA = 0.08
    GAMMA = 0.005
    BETA = 0.5

    # 시총 구간별 기본 슬리피지 (Fallback용)
    SLIPPAGE_BY_CAP = {
        "mega": {"threshold": 10_000_000_000_000, "slippage": 0.0004},
        "large": {"threshold": 1_000_000_000_000, "slippage": 0.0012},
        "mid": {"threshold": 100_000_000_000, "slippage": 0.0025},
        "small": {"threshold": 0, "slippage": 0.007},
    }

    def __init__(self, max_slippage_bps: float = 100.0, num_slices: int = 3):
        self.max_slippage_bps = max_slippage_bps
        self.num_slices = max(1, num_slices)

    def get_session(self, timestamp: datetime | None = None) -> MarketSession:
        if timestamp is None:
            timestamp = datetime.now()
        t = timestamp.time()
        if time(8, 30) <= t < time(9, 0):
            return MarketSession.PRE_OPEN
        elif time(9, 0) <= t < time(15, 20):
            return MarketSession.REGULAR
        elif time(15, 20) <= t < time(15, 30):
            return MarketSession.CLOSING_AUCTION
        elif time(16, 0) <= t < time(18, 0):
            return MarketSession.AFTER_HOURS
        else:
            return MarketSession.CLOSED

    def execute(
        self,
        ticker: str,
        action: str,
        price: float,
        volume: int,
        order_size: int,
        market_cap: float,
        avg_daily_volume: int = 0,
        current_time: datetime | None = None,
        orderbook: dict | None = None,
    ) -> ExecutionResult:
        if current_time is None:
            current_time = datetime.now()

        session = self.get_session(current_time)
        if session not in [MarketSession.REGULAR, MarketSession.CLOSING_AUCTION]:
            return ExecutionResult(
                filled=False,
                fill_ratio=0.0,
                execution_price=price,
                slippage_bps=0.0,
                market_impact_bps=0.0,
                commission=0.0,
                tax=0.0,
                total_cost=0.0,
                reason=f"거래 불가 세션: {session.value}",
                remaining_volume=order_size,
            )

        # 1. 시장 충격 계산
        market_impact_bps = self._calculate_market_impact(order_size, avg_daily_volume, price, market_cap)

        # 2. 시간 분할 체결 (Slicing)
        slice_size = max(1, order_size // self.num_slices)
        total_filled = 0
        total_cost = 0.0
        total_slippage_bps = 0.0
        executed_slices = 0

        for i in range(self.num_slices):
            remaining_order = order_size - total_filled
            if remaining_order <= 0:
                break

            current_slice_size = min(slice_size, remaining_order)

            if orderbook and self._has_valid_orderbook(orderbook):
                result = self._execute_slice_with_orderbook(
                    action, price, current_slice_size, orderbook, market_impact_bps, i
                )
            else:
                result = self._execute_slice_fallback(
                    action, price, current_slice_size, market_cap, avg_daily_volume, market_impact_bps
                )

            if result.filled and result.fill_ratio > 0:
                filled_in_slice = int(result.fill_ratio * current_slice_size)
                total_filled += filled_in_slice
                total_cost += result.execution_price * filled_in_slice
                total_slippage_bps += result.slippage_bps * (result.fill_ratio)
                executed_slices += 1

            # 🔥 v3.2: asyncio.sleep() 호출 제거 (실제 대기 없이 경고만 유발하던 버그)
            # 슬라이스 간 시간차는 논리적 순서만으로 충분함

        remaining_volume = order_size - total_filled

        if total_filled == 0:
            return ExecutionResult(
                filled=False,
                fill_ratio=0.0,
                execution_price=price,
                slippage_bps=0.0,
                market_impact_bps=market_impact_bps,
                commission=0.0,
                tax=0.0,
                total_cost=0.0,
                reason="모든 슬라이스 체결 실패",
                remaining_volume=remaining_volume,
            )

        avg_price = total_cost / total_filled
        fill_ratio = total_filled / order_size
        total_slippage_bps = total_slippage_bps / executed_slices if executed_slices > 0 else 0
        total_slippage_bps += market_impact_bps

        # 최대 슬리피지 제한
        if abs(total_slippage_bps) > self.max_slippage_bps:
            total_slippage_bps = self.max_slippage_bps if total_slippage_bps > 0 else -self.max_slippage_bps
            avg_price = price * (1 + total_slippage_bps / 10000)

        commission = avg_price * self.BROKERAGE_FEE
        tax = avg_price * self.SECURITIES_TAX if action.upper() == "SELL" else 0.0
        total_cost_with_fee = total_cost + commission + tax

        return ExecutionResult(
            filled=True,
            fill_ratio=fill_ratio,
            execution_price=avg_price,
            slippage_bps=total_slippage_bps,
            market_impact_bps=market_impact_bps,
            commission=commission,
            tax=tax,
            total_cost=total_cost_with_fee,
            reason=f"체결 {fill_ratio:.1%} ({executed_slices}슬라이스)",
            slices=executed_slices,
            remaining_volume=remaining_volume,
        )

    # ============================================================
    # 내부 메서드
    # ============================================================
    def _calculate_market_impact(
        self, order_size: int, avg_daily_volume: int, price: float, market_cap: float
    ) -> float:
        """Almgren-Chriss 시장 충격 모델 (v3.1 튜닝 적용)"""
        if avg_daily_volume <= 0 or price <= 0:
            return 0.0

        participation = order_size / avg_daily_volume
        if participation <= 0.001:
            return 0.0

        temp_impact = self.ALPHA * (participation**self.BETA) * 10000
        perm_impact = self.GAMMA * participation * 10000

        total_impact_bps = temp_impact + perm_impact
        return min(total_impact_bps, self.max_slippage_bps)

    def _has_valid_orderbook(self, orderbook: dict) -> bool:
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        return isinstance(bids, list) and len(bids) > 0 and isinstance(asks, list) and len(asks) > 0

    def _execute_slice_with_orderbook(
        self, action: str, ref_price: float, slice_size: int, orderbook: dict, impact_bps: float, slice_idx: int
    ) -> ExecutionResult:
        if action.upper() == "BUY":
            levels = sorted(orderbook.get("asks", []), key=lambda x: x[0])
        else:
            levels = sorted(orderbook.get("bids", []), key=lambda x: x[0], reverse=True)

        if not levels:
            return self._empty_result(ref_price, "호가 없음", slice_size)

        remaining = slice_size
        total_cost = 0.0
        filled_qty = 0

        for price, qty in levels:
            if remaining <= 0:
                break
            fill = min(remaining, qty)
            impact_adjustment = (impact_bps / 10000) * (1 + (filled_qty / slice_size) * 0.5)
            if action.upper() == "BUY":
                exec_price = price * (1 + impact_adjustment)
            else:
                exec_price = price * (1 - impact_adjustment)
            total_cost += exec_price * fill
            filled_qty += fill
            remaining -= fill

        if filled_qty == 0:
            return self._empty_result(ref_price, "체결 불가", slice_size)

        avg_price = total_cost / filled_qty
        fill_ratio = filled_qty / slice_size
        slippage = (avg_price - ref_price) / ref_price * 10000

        return ExecutionResult(
            filled=True,
            fill_ratio=fill_ratio,
            execution_price=avg_price,
            slippage_bps=slippage,
            market_impact_bps=impact_bps,
            commission=0,
            tax=0,
            total_cost=total_cost,
            reason=f"슬라이스 {slice_idx+1} 체결",
            remaining_volume=remaining,
        )

    def _execute_slice_fallback(
        self, action: str, price: float, slice_size: int, market_cap: float, avg_daily_volume: int, impact_bps: float
    ) -> ExecutionResult:
        """Fallback 정밀화 (시총 + 평균 거래량 고려)"""
        base_slip = self._calculate_base_slippage(market_cap)

        volume_factor = 0.0
        if avg_daily_volume > 0:
            participation = slice_size / avg_daily_volume
            if participation > 0.05:
                volume_factor = min(0.5, (participation - 0.05) * 2.0)

        total_slip_bps = (base_slip * 10000) + (volume_factor * 10000) + impact_bps
        total_slip_bps = min(total_slip_bps, self.max_slippage_bps)

        if action.upper() == "BUY":
            exec_price = price * (1 + total_slip_bps / 10000)
        else:
            exec_price = price * (1 - total_slip_bps / 10000)

        return ExecutionResult(
            filled=True,
            fill_ratio=1.0,
            execution_price=exec_price,
            slippage_bps=total_slip_bps,
            market_impact_bps=impact_bps,
            commission=0,
            tax=0,
            total_cost=exec_price * slice_size,
            reason="Fallback (시총+거래량 반영)",
            remaining_volume=0,
        )

    def _calculate_base_slippage(self, market_cap: float) -> float:
        for tier, config in sorted(self.SLIPPAGE_BY_CAP.items(), key=lambda x: x[1]["threshold"], reverse=True):
            if market_cap >= config["threshold"]:
                return config["slippage"]
        return self.SLIPPAGE_BY_CAP["small"]["slippage"]

    def _empty_result(self, ref_price: float, reason: str, remaining: int) -> ExecutionResult:
        return ExecutionResult(
            filled=False,
            fill_ratio=0.0,
            execution_price=ref_price,
            slippage_bps=0.0,
            market_impact_bps=0.0,
            commission=0.0,
            tax=0.0,
            total_cost=0.0,
            reason=reason,
            remaining_volume=remaining,
        )
