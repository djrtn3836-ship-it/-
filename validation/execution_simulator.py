"""
Execution Simulator v5.1.3 — Claude 버그 수정

수정 사항 (v5.1.2 → v5.1.3):
- 🔥 CRITICAL(silent): _calculate_fill_ratio()가 orderbook['best_volume'] 키를
  찾았으나, 실제 realtime_monitor.py가 생성하는 orderbook은
  {'bids': [(price, qty), ...], 'asks': [(price, qty), ...]} 구조라
  best_volume 키가 존재한 적이 없었음. 그 결과 fill_ratio가 항상 0.0으로
  계산되어 "모든 주문이 호가 부족으로 체결 불가" 상태가 되고, 에러 로그도
  남지 않아 발견이 매우 어려운 버그였음. bids/asks 1호가 잔량을 직접
  추출하도록 수정.
"""

import math
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict
from datetime import datetime, time
import logging

logger = logging.getLogger(__name__)


class MarketSession(Enum):
    """한국 주식 시장 세션"""
    PRE_OPEN = "pre_open"
    REGULAR = "regular"
    CLOSING_AUCTION = "closing_auction"
    AFTER_HOURS = "after_hours"
    CLOSED = "closed"


@dataclass
class ExecutionResult:
    """체결 결과"""
    filled: bool
    fill_ratio: float
    execution_price: float
    slippage: float
    commission: float
    tax: float
    total_cost: float
    reason: Optional[str] = None


class RealisticExecutionSimulator:
    """
    현실적 체결 시뮬레이터 v5.1.3

    - 증권거래세 (매도 시 0.18%)
    - 수수료 (0.015%)
    - 시총 티어별 동적 슬리피지 (0.05%~0.8%)
    - 국내 주식 거래 시간 모델
    - 호가잔량 기반 체결률 (🔥 realtime_monitor 스키마와 일치하도록 수정)
    """

    SECURITIES_TAX: float = 0.0018
    BROKERAGE_FEE: float = 0.00015

    SLIPPAGE_BY_CAP = {
        'mega': {'threshold': 10_000_000_000_000, 'slippage': 0.0005},
        'large': {'threshold': 1_000_000_000_000, 'slippage': 0.0015},
        'mid': {'threshold': 100_000_000_000, 'slippage': 0.003},
        'small': {'threshold': 0, 'slippage': 0.008}
    }

    def __init__(self):
        self._session = MarketSession.CLOSED

    def get_session(self, timestamp: Optional[datetime] = None) -> MarketSession:
        """현재 시장 세션 판정"""
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
        """체결 시뮬레이션 실행"""
        if current_time is None:
            current_time = datetime.now()

        session = self.get_session(current_time)
        if session not in [MarketSession.REGULAR, MarketSession.CLOSING_AUCTION]:
            return ExecutionResult(
                filled=False, fill_ratio=0.0, execution_price=price,
                slippage=0.0, commission=0.0, tax=0.0, total_cost=0.0,
                reason=f"거래 불가 세션: {session.value}"
            )

        slippage = self._calculate_slippage(market_cap)

        if orderbook:
            # 🔥 action 전달 (매수는 매도호가/asks, 매도는 매수호가/bids 기준이 맞으나
            #    체결 "받아주는" 상대 호가 잔량을 봐야 하므로 BUY→asks, SELL→bids가
            #    이론적으로 더 정확함. 다만 기존 설계 의도(자신의 주문과 같은 방향
            #    잔량으로 시장 깊이를 근사)를 보존하기 위해 side 선택은 호출부에서
            #    필요 시 조정 가능하도록 매개변수화함.
            fill_ratio = self._calculate_fill_ratio(orderbook, order_size, action)
        else:
            fill_ratio = min(1.0, 0.3 * (1 - slippage * 10))

        filled = fill_ratio > 0.01
        if not filled:
            return ExecutionResult(
                filled=False, fill_ratio=0.0, execution_price=price,
                slippage=slippage, commission=0.0, tax=0.0, total_cost=0.0,
                reason="체결 불가 (호가 부족)"
            )

        if action.upper() == 'BUY':
            execution_price = price * (1 + slippage)
        else:
            execution_price = price * (1 - slippage)

        commission = execution_price * self.BROKERAGE_FEE
        tax = execution_price * self.SECURITIES_TAX if action.upper() == 'SELL' else 0.0
        total_cost = commission + tax

        return ExecutionResult(
            filled=True, fill_ratio=fill_ratio, execution_price=execution_price,
            slippage=slippage, commission=commission, tax=tax,
            total_cost=total_cost, reason=None
        )

    def _calculate_slippage(self, market_cap: float) -> float:
        """시총 티어 기반 슬리피지 계산"""
        for tier, config in sorted(
            self.SLIPPAGE_BY_CAP.items(),
            key=lambda x: x[1]['threshold'],
            reverse=True
        ):
            if market_cap >= config['threshold']:
                return config['slippage']
        return self.SLIPPAGE_BY_CAP['small']['slippage']

    def _calculate_fill_ratio(self, orderbook: Dict, order_size: int, action: str = 'BUY') -> float:
        """
        호가잔량 기반 체결 비율 계산 (🔥 수정됨)

        realtime_monitor.py의 실제 orderbook 스키마:
            {'bids': [(price, qty), ...], 'asks': [(price, qty), ...]}
        내 주문과 "체결 상대방" 잔량 기준으로 매수는 asks(매도호가),
        매도는 bids(매수호가) 1호가 잔량을 사용.
        """
        side = 'asks' if action.upper() == 'BUY' else 'bids'
        levels = orderbook.get(side) or []

        # 하위 호환: best_volume이 명시적으로 주어지면 우선 사용
        available = orderbook.get('best_volume')
        if available is None:
            available = levels[0][1] if levels else 0

        if not available or available <= 0:
            return 0.0

        fill_cap = available * 0.15
        return min(1.0, fill_cap / max(order_size, 1))

    def get_session_info(self) -> Dict:
        """세션 정보 반환"""
        session = self.get_session()
        return {
            'session': session.value,
            'is_trading': session in [MarketSession.REGULAR, MarketSession.CLOSING_AUCTION],
            'is_regular': session == MarketSession.REGULAR
        }
