"""
validation/execution_simulator.py - v3.0 (Almgren-Chriss 시장 충격 + 시간 분할)
- 영구/임시 시장 충격 함수 (Almgren-Chriss 모델)
- 1초당 3슬라이스 분할 체결 (Time-sliced)
- 다중 호가 레벨 순회 + 부분 체결 정밀화
"""

import math
import random
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, List
from datetime import datetime, time

import logging
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
    fill_ratio: float          # 0~1
    execution_price: float     # 평균 체결가
    slippage_bps: float        # 전체 슬리피지 (bp)
    market_impact_bps: float   # 시장 충격에 의한 추가 슬리피지
    commission: float
    tax: float
    total_cost: float
    reason: str = ""
    slices: int = 0            # 분할 체결 슬라이스 수


class RealisticExecutionSimulator:
    SECURITIES_TAX = 0.0018
    BROKERAGE_FEE = 0.00015

    # Almgren-Chriss 파라미터 (경험적 튜닝)
    ALPHA = 0.1      # 임시 충격 계수
    GAMMA = 0.01     # 영구 충격 계수
    BETA = 0.5       # 충격 지수

    def __init__(self, max_slippage_bps: float = 100.0, num_slices: int = 3):
        self.max_slippage_bps = max_slippage_bps
        self.num_slices = max(1, num_slices)  # 최소 1회

    def get_session(self, timestamp: Optional[datetime] = None) -> MarketSession:
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
        current_time: Optional[datetime] = None,
        orderbook: Optional[Dict] = None
    ) -> ExecutionResult:
        if current_time is None:
            current_time = datetime.now()

        session = self.get_session(current_time)
        if session not in [MarketSession.REGULAR, MarketSession.CLOSING_AUCTION]:
            return ExecutionResult(
                filled=False, fill_ratio=0.0, execution_price=price,
                slippage_bps=0.0, market_impact_bps=0.0,
                commission=0.0, tax=0.0, total_cost=0.0,
                reason=f"거래 불가 세션: {session.value}"
            )

        # 1. 시장 충격 계산 (Almgren-Chriss)
        market_impact_bps = self._calculate_market_impact(
            order_size, avg_daily_volume, price, market_cap
        )

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
            
            # 호가 데이터가 있으면 정밀 시뮬레이션
            if orderbook and self._has_valid_orderbook(orderbook):
                result = self._execute_slice_with_orderbook(
                    action, price, current_slice_size, orderbook, market_impact_bps, i
                )
            else:
                result = self._execute_slice_fallback(
                    action, price, current_slice_size, market_cap, market_impact_bps
                )

            if result.filled and result.fill_ratio > 0:
                total_filled += int(result.fill_ratio * current_slice_size)
                total_cost += result.execution_price * int(result.fill_ratio * current_slice_size)
                total_slippage_bps += result.slippage_bps * (result.fill_ratio)
                executed_slices += 1

            # 슬라이스 간 0.3초 대기 (실제 체결 시간 차이)
            if i < self.num_slices - 1:
                import asyncio
                asyncio.sleep(0.3)  # 동기 호출 시 주의 (실제로는 async)

        # 결과 집계
        if total_filled == 0:
            return ExecutionResult(
                filled=False, fill_ratio=0.0, execution_price=price,
                slippage_bps=0.0, market_impact_bps=market_impact_bps,
                commission=0.0, tax=0.0, total_cost=0.0,
                reason="모든 슬라이스 체결 실패"
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
        tax = avg_price * self.SECURITIES_TAX if action.upper() == 'SELL' else 0.0
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
            slices=executed_slices
        )

    # ============================================================
    # 내부 메서드
    # ============================================================
    def _calculate_market_impact(self, order_size: int, avg_daily_volume: int, price: float, market_cap: float) -> float:
        """Almgren-Chriss 시장 충격 모델 (bp)"""
        if avg_daily_volume <= 0 or price <= 0:
            return 0.0

        participation = order_size / avg_daily_volume
        if participation <= 0.001:  # 0.1% 미만은 충격 없음
            return 0.0

        # 임시 충격 (Temporary Impact): sqrt(participation)
        temp_impact = self.ALPHA * (participation ** self.BETA) * 10000  # bp
        # 영구 충격 (Permanent Impact): linear
        perm_impact = self.GAMMA * participation * 10000  # bp

        total_impact_bps = temp_impact + perm_impact
        return min(total_impact_bps, self.max_slippage_bps)

    def _has_valid_orderbook(self, orderbook: Dict) -> bool:
        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])
        return (isinstance(bids, list) and len(bids) > 0 and
                isinstance(asks, list) and len(asks) > 0)

    def _execute_slice_with_orderbook(self, action: str, ref_price: float, slice_size: int,
                                       orderbook: Dict, impact_bps: float, slice_idx: int) -> ExecutionResult:
        if action.upper() == 'BUY':
            levels = sorted(orderbook.get('asks', []), key=lambda x: x[0])
        else:
            levels = sorted(orderbook.get('bids', []), key=lambda x: x[0], reverse=True)

        if not levels:
            return self._empty_result(ref_price, "호가 없음")

        remaining = slice_size
        total_cost = 0.0
        filled_qty = 0

        for price, qty in levels:
            if remaining <= 0:
                break
            fill = min(remaining, qty)
            # 시장 충격 반영: 호가를 소진할수록 가격이 불리해짐
            impact_adjustment = (impact_bps / 10000) * (1 + (filled_qty / slice_size) * 0.5)
            if action.upper() == 'BUY':
                exec_price = price * (1 + impact_adjustment)
            else:
                exec_price = price * (1 - impact_adjustment)
            total_cost += exec_price * fill
            filled_qty += fill
            remaining -= fill

        if filled_qty == 0:
            return self._empty_result(ref_price, "체결 불가")

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
            reason=f"슬라이스 {slice_idx+1} 체결"
        )

    def _execute_slice_fallback(self, action: str, price: float, slice_size: int,
                                 market_cap: float, impact_bps: float) -> ExecutionResult:
        # 시총 기반 기본 슬리피지
        base_slip = self._calculate_base_slippage(market_cap)
        total_slip_bps = base_slip * 10000 + impact_bps
        total_slip_bps = min(total_slip_bps, self.max_slippage_bps)

        if action.upper() == 'BUY':
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
            reason="Fallback (단일 슬라이스)"
        )

    def _calculate_base_slippage(self, market_cap: float) -> float:
        if market_cap >= 10_000_000_000_000:
            return 0.0005
        elif market_cap >= 1_000_000_000_000:
            return 0.0015
        elif market_cap >= 100_000_000_000:
            return 0.003
        else:
            return 0.008

    def _empty_result(self, ref_price: float, reason: str) -> ExecutionResult:
        return ExecutionResult(
            filled=False, fill_ratio=0.0, execution_price=ref_price,
            slippage_bps=0.0, market_impact_bps=0.0,
            commission=0.0, tax=0.0, total_cost=0.0, reason=reason
        )