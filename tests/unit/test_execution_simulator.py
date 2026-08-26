"""
tests/unit/test_execution_simulator.py - Execution Simulator 단위 테스트
"""

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from validation.execution_simulator import RealisticExecutionSimulator


class TestExecutionSimulator:
    """ExecutionSimulator 단위 테스트"""

    def setup_method(self):
        self.sim = RealisticExecutionSimulator(max_slippage_bps=100.0, num_slices=3)

    def test_market_impact_calculation(self, sample_orderbook):
        """시장 충격 계산 테스트 (Almgren-Chriss)"""
        # 주문량이 평균 거래량의 5%일 때
        impact = self.sim._calculate_market_impact(
            order_size=75000,  # 평균 1,500,000주의 5%
            avg_daily_volume=1500000,
            price=83000.0,
            market_cap=1_000_000_000_000,
        )
        # 영향이 0보다 크고, 최대 슬리피지(100bp)보다 작아야 함
        assert 0 < impact <= 100.0
        print(f"✅ 시장 충격 테스트 통과: {impact:.2f}bp")

    def test_orderbook_execution_buy(self, sample_orderbook):
        """호가 기반 매수 체결 테스트"""
        result = self.sim._execute_slice_with_orderbook(
            action="BUY", ref_price=83000.0, slice_size=500, orderbook=sample_orderbook, impact_bps=10.0, slice_idx=0
        )
        assert result.filled is True
        assert result.fill_ratio > 0
        assert result.execution_price >= 83000.0  # 매수는 호가보다 비싸야 함
        assert result.remaining_volume == 0
        print(f"✅ 매수 체결 테스트 통과: 체결가 {result.execution_price:.0f} (fill_ratio {result.fill_ratio:.2f})")

    def test_orderbook_execution_sell(self, sample_orderbook):
        """호가 기반 매도 체결 테스트"""
        result = self.sim._execute_slice_with_orderbook(
            action="SELL", ref_price=83000.0, slice_size=300, orderbook=sample_orderbook, impact_bps=10.0, slice_idx=0
        )
        assert result.filled is True
        assert result.fill_ratio > 0
        assert result.execution_price <= 83000.0  # 매도는 호가보다 싸야 함
        print(f"✅ 매도 체결 테스트 통과: 체결가 {result.execution_price:.0f} (fill_ratio {result.fill_ratio:.2f})")

    def test_fallback_execution(self):
        """Fallback 체결 테스트 (호가 없을 때)"""
        result = self.sim._execute_slice_fallback(
            action="BUY",
            price=83000.0,
            slice_size=1000,
            market_cap=500_000_000_000,  # 중형주
            avg_daily_volume=500000,
            impact_bps=5.0,
        )
        assert result.filled is True
        assert result.fill_ratio == 1.0
        assert result.slippage_bps > 0
        print(f"✅ Fallback 테스트 통과: 슬리피지 {result.slippage_bps:.1f}bp")

    def test_partial_fill(self, sample_orderbook):
        """부분 체결 테스트 (잔량 부족)"""
        # 주문량이 호가보다 많을 때 (bids/asks 합계: 800+600+300=1700)
        result = self.sim._execute_slice_with_orderbook(
            action="BUY",
            ref_price=83000.0,
            slice_size=2000,  # 1700보다 많음
            orderbook=sample_orderbook,
            impact_bps=10.0,
            slice_idx=0,
        )
        # 체결은 되지만, fill_ratio는 1.0 미만이어야 함
        assert result.filled is True
        assert result.fill_ratio < 1.0
        assert result.remaining_volume > 0
        print(f"✅ 부분 체결 테스트 통과: fill_ratio {result.fill_ratio:.2f}, 잔량 {result.remaining_volume}")

    def test_full_execution_with_slicing(self, sample_orderbook):
        """3분할 체결 통합 테스트"""
        result = self.sim.execute(
            ticker="005930",
            action="BUY",
            price=83000.0,
            volume=1500,
            order_size=1500,
            market_cap=1_000_000_000_000,
            avg_daily_volume=1500000,
            current_time=datetime(2024, 1, 15, 10, 30, 0),  # 거래 시간(10:30) 고정
            orderbook=sample_orderbook,
        )
        assert result.filled is True
        assert result.fill_ratio > 0.8  # 대부분 체결
        assert result.slices == 3  # 3분할 실행
        print(f"✅ 전체 실행 테스트 통과: fill_ratio {result.fill_ratio:.2f}, 슬라이스 {result.slices}개")
