"""
validation/execution_simulator.py - v2.0 (호가깊이 기반 체결 시뮬레이터)
- 기존 1호가 잔량만 사용하던 것을 여러 호가 레벨을 순회하며 체결량 누적
- 매수/매도 시 실제 체결 가능한 평균 가격과 슬리피지 산출
- 부분 체결 지원
- 세션 체크, 수수료/세금, 시총 기반 슬리피지 Fallback 유지
"""

import math
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
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
    slippage_bps: float        # 슬리피지 (bp)
    commission: float
    tax: float
    total_cost: float
    reason: Optional[str] = None


class RealisticExecutionSimulator:
    SECURITIES_TAX: float = 0.0018
    BROKERAGE_FEE: float = 0.00015

    SLIPPAGE_BY_CAP = {
        'mega': {'threshold': 10_000_000_000_000, 'slippage': 0.0005},
        'large': {'threshold': 1_000_000_000_000, 'slippage': 0.0015},
        'mid': {'threshold': 100_000_000_000, 'slippage': 0.003},
        'small': {'threshold': 0, 'slippage': 0.008}
    }

    def __init__(self, max_slippage_bps: float = 100.0):
        self.max_slippage_bps = max_slippage_bps
        self._session = MarketSession.CLOSED

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
        current_time: Optional[datetime] = None,
        orderbook: Optional[Dict] = None
    ) -> ExecutionResult:
        if current_time is None:
            current_time = datetime.now()

        session = self.get_session(current_time)
        if session not in [MarketSession.REGULAR, MarketSession.CLOSING_AUCTION]:
            return ExecutionResult(
                filled=False, fill_ratio=0.0, execution_price=price,
                slippage_bps=0.0, commission=0.0, tax=0.0, total_cost=0.0,
                reason=f"거래 불가 세션: {session.value}"
            )

        # 호가 데이터가 있으면 정밀 시뮬레이션, 없으면 Fallback
        if orderbook and self._has_valid_orderbook(orderbook):
            return self._execute_with_orderbook(action, price, order_size, orderbook)
        else:
            return self._execute_fallback(action, price, order_size, market_cap)

    def _has_valid_orderbook(self, orderbook: Dict) -> bool:
        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])
        return (isinstance(bids, list) and len(bids) > 0 and
                isinstance(asks, list) and len(asks) > 0)

    def _execute_with_orderbook(self, action: str, ref_price: float, order_size: int, orderbook: Dict) -> ExecutionResult:
        """
        호가 깊이를 순회하며 실제 체결 가능한 평균 가격과 체결률 계산
        """
        if action.upper() == 'BUY':
            # 매수는 매도호가(asks)를 낮은 가격부터 소진
            levels = sorted(orderbook.get('asks', []), key=lambda x: x[0])
        else:  # SELL
            # 매도는 매수호가(bids)를 높은 가격부터 소진
            levels = sorted(orderbook.get('bids', []), key=lambda x: x[0], reverse=True)

        if not levels:
            return ExecutionResult(
                filled=False, fill_ratio=0.0, execution_price=ref_price,
                slippage_bps=0.0, commission=0.0, tax=0.0, total_cost=0.0,
                reason="호가 데이터 없음"
            )

        remaining = order_size
        total_cost = 0.0
        filled_qty = 0
        last_price = ref_price

        for price, qty in levels:
            if remaining <= 0:
                break
            fill = min(remaining, qty)
            total_cost += price * fill
            filled_qty += fill
            remaining -= fill
            last_price = price

        if filled_qty == 0:
            return ExecutionResult(
                filled=False, fill_ratio=0.0, execution_price=ref_price,
                slippage_bps=0.0, commission=0.0, tax=0.0, total_cost=0.0,
                reason="체결 불가 (호가 잔량 부족)"
            )

        fill_ratio = filled_qty / order_size
        avg_price = total_cost / filled_qty

        # 슬리피지 (bp)
        if ref_price > 0:
            slippage_bps = (avg_price - ref_price) / ref_price * 10000
        else:
            slippage_bps = 0.0

        # 최대 슬리피지 제한
        if abs(slippage_bps) > self.max_slippage_bps:
            slippage_bps = self.max_slippage_bps if slippage_bps > 0 else -self.max_slippage_bps
            avg_price = ref_price * (1 + slippage_bps / 10000)

        commission = avg_price * self.BROKERAGE_FEE
        tax = avg_price * self.SECURITIES_TAX if action.upper() == 'SELL' else 0.0
        total_cost_with_fee = total_cost + commission + tax

        return ExecutionResult(
            filled=True,
            fill_ratio=fill_ratio,
            execution_price=avg_price,
            slippage_bps=slippage_bps,
            commission=commission,
            tax=tax,
            total_cost=total_cost_with_fee,
            reason=f"체결 {fill_ratio:.1%} (호가 {len(levels)}개 소진)"
        )

    def _execute_fallback(self, action: str, price: float, order_size: int, market_cap: float) -> ExecutionResult:
        """호가 데이터 없을 때 시총 기반 슬리피지 추정"""
        slippage = self._calculate_slippage(market_cap)
        # 주문량이 많을수록 슬리피지 증가
        volume_factor = min(1.0, order_size / 1000) * 0.5
        slippage = slippage * (1 + volume_factor)

        if action.upper() == 'BUY':
            exec_price = price * (1 + slippage)
        else:
            exec_price = price * (1 - slippage)

        commission = exec_price * self.BROKERAGE_FEE
        tax = exec_price * self.SECURITIES_TAX if action.upper() == 'SELL' else 0.0
        total_cost = exec_price * order_size + commission + tax

        return ExecutionResult(
            filled=True,
            fill_ratio=1.0,
            execution_price=exec_price,
            slippage_bps=slippage * 10000,
            commission=commission,
            tax=tax,
            total_cost=total_cost,
            reason="Fallback (호가 데이터 없음)"
        )

    def _calculate_slippage(self, market_cap: float) -> float:
        for tier, config in sorted(
            self.SLIPPAGE_BY_CAP.items(),
            key=lambda x: x[1]['threshold'],
            reverse=True
        ):
            if market_cap >= config['threshold']:
                return config['slippage']
        return self.SLIPPAGE_BY_CAP['small']['slippage']

    def get_session_info(self) -> Dict:
        session = self.get_session()
        return {
            'session': session.value,
            'is_trading': session in [MarketSession.REGULAR, MarketSession.CLOSING_AUCTION],
            'is_regular': session == MarketSession.REGULAR
        }