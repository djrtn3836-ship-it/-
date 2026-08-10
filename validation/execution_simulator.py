"""
Execution Simulator v5.1.2 — 현실적 거래비용 반영

변경사항:
1. 한국 시장 거래비용 반영 (증권거래세, 수수료)
2. 시총 티어별 동적 슬리피지
3. 국내 주식 거래 시간 모델 추가
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
    현실적 체결 시뮬레이터 v5.1.2
    
    변경사항:
    - 증권거래세 (매도 시 0.18%)
    - 수수료 (0.015%)
    - 시총 티어별 동적 슬리피지 (0.05%~0.8%)
    - 국내 주식 거래 시간 모델
    - 호가잔량 기반 체결률
    """
    
    # ===== 거래비용 =====
    SECURITIES_TAX: float = 0.0018        # 증권거래세 (매도 시)
    BROKERAGE_FEE: float = 0.00015        # 수수료
    
    # ===== 시총 티어별 슬리피지 =====
    SLIPPAGE_BY_CAP = {
        'mega': {'threshold': 10_000_000_000_000, 'slippage': 0.0005},   # 10조 이상: 0.05%
        'large': {'threshold': 1_000_000_000_000, 'slippage': 0.0015},    # 1조 이상: 0.15%
        'mid': {'threshold': 100_000_000_000, 'slippage': 0.003},         # 1천억 이상: 0.3%
        'small': {'threshold': 0, 'slippage': 0.008}                      # 그 외: 0.8%
    }
    
    def __init__(self):
        self._session = MarketSession.CLOSED
    
    def get_session(self, timestamp: Optional[datetime] = None) -> MarketSession:
        """현재 시장 세션 판정"""
        if timestamp is None:
            timestamp = datetime.now()
        
        t = timestamp.time()
        
        # 장전 동시호가
        if time(8, 30) <= t < time(9, 0):
            return MarketSession.PRE_OPEN
        # 정규장
        elif time(9, 0) <= t < time(15, 20):
            return MarketSession.REGULAR
        # 장마감 동시호가
        elif time(15, 20) <= t < time(15, 30):
            return MarketSession.CLOSING_AUCTION
        # 시간외
        elif time(16, 0) <= t < time(18, 0):
            return MarketSession.AFTER_HOURS
        else:
            return MarketSession.CLOSED
    
    def execute(
        self,
        ticker: str,
        action: str,           # 'BUY' or 'SELL'
        price: float,
        volume: int,
        order_size: int,
        market_cap: float,
        current_time: Optional[datetime] = None,
        orderbook: Optional[Dict] = None
    ) -> ExecutionResult:
        """
        체결 시뮬레이션 실행
        """
        if current_time is None:
            current_time = datetime.now()
        
        # 1. 세션 체크
        session = self.get_session(current_time)
        if session not in [MarketSession.REGULAR, MarketSession.CLOSING_AUCTION]:
            return ExecutionResult(
                filled=False,
                fill_ratio=0.0,
                execution_price=price,
                slippage=0.0,
                commission=0.0,
                tax=0.0,
                total_cost=0.0,
                reason=f"거래 불가 세션: {session.value}"
            )
        
        # 2. 슬리피지 계산 (시총 티어 기반)
        slippage = self._calculate_slippage(market_cap)
        
        # 3. 체결 비율 계산 (호가잔량 기반)
        if orderbook:
            fill_ratio = self._calculate_fill_ratio(orderbook, order_size)
        else:
            # 호가 없으면 30% 기본 체결 가정
            fill_ratio = min(1.0, 0.3 * (1 - slippage * 10))
        
        # 4. 체결 결정
        filled = fill_ratio > 0.01
        if not filled:
            return ExecutionResult(
                filled=False,
                fill_ratio=0.0,
                execution_price=price,
                slippage=slippage,
                commission=0.0,
                tax=0.0,
                total_cost=0.0,
                reason="체결 불가 (호가 부족)"
            )
        
        # 5. 체결 가격
        if action.upper() == 'BUY':
            execution_price = price * (1 + slippage)
        else:  # SELL
            execution_price = price * (1 - slippage)
        
        # 6. 수수료 계산
        commission = execution_price * self.BROKERAGE_FEE
        
        # 7. 증권거래세 (매도 시만)
        tax = execution_price * self.SECURITIES_TAX if action.upper() == 'SELL' else 0.0
        
        # 8. 총 비용
        total_cost = commission + tax
        
        return ExecutionResult(
            filled=True,
            fill_ratio=fill_ratio,
            execution_price=execution_price,
            slippage=slippage,
            commission=commission,
            tax=tax,
            total_cost=total_cost,
            reason=None
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
    
    def _calculate_fill_ratio(self, orderbook: Dict, order_size: int) -> float:
        """호가잔량 기반 체결 비율 계산"""
        # 매수/매도 1호가 잔량의 15~20%만 체결 가정
        available = orderbook.get('best_volume', 0)
        if available <= 0:
            return 0.0
        # 잔량의 15%만 체결 가능 (호가 부족 시)
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