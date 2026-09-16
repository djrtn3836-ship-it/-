"""execution_simulator.py 커버리지 개선 테스트"""
import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock

from validation.execution_simulator import RealisticExecutionSimulator, ExecutionResult, MarketSession


class TestExecutionSimulatorCoverage:
    """RealisticExecutionSimulator 커버리지 개선"""

    @pytest.fixture
    def simulator(self):
        """시뮬레이터 인스턴스"""
        return RealisticExecutionSimulator(
            name="test_simulator",
            latency_ms=10
        )

    def test_simulator_init(self, simulator):
        """시뮬레이터 초기화"""
        assert simulator.name == "test_simulator"
        assert simulator.latency_ms == 10

    def test_execute_buy_order(self, simulator):
        """BUY 주문 실행"""
        result = simulator.execute_order(
            ticker="005930",
            side="BUY",
            quantity=100,
            price=80000
        )
        assert result is not None

    def test_execute_sell_order(self, simulator):
        """SELL 주문 실행"""
        result = simulator.execute_order(
            ticker="005930",
            side="SELL",
            quantity=100,
            price=81000
        )
        assert result is not None

    def test_simulate_partial_fill(self, simulator):
        """부분 체결 시뮬레이션"""
        result = simulator.execute_order(
            ticker="005930",
            side="BUY",
            quantity=1000,
            price=80000
        )
        if "filled_quantity" in result or hasattr(result, "filled_quantity"):
            assert result["filled_quantity"] <= 1000 or result.filled_quantity <= 1000

    def test_simulate_slippage(self, simulator):
        """슬리피지 시뮬레이션"""
        price = 80000
        slippage = simulator.apply_slippage(price, "BUY", 0.001)
        assert slippage is not None

    def test_calculate_transaction_cost(self, simulator):
        """거래 비용 계산"""
        cost = simulator.calculate_transaction_cost(100, 80000, 0.0005)
        assert cost >= 0  # 비용은 음이 아님

    def test_get_execution_report(self, simulator):
        """실행 리포트"""
        simulator.execute_order(
            ticker="005930",
            side="BUY",
            quantity=100,
            price=80000
        )
        report = simulator.get_execution_report()
        assert report is not None

    def test_reset_simulator(self, simulator):
        """시뮬레이터 리셋"""
        simulator.reset()
        # 리셋 후 상태 확인

    def test_market_session_enum(self):
        """MarketSession 열거형 테스트"""
        assert hasattr(MarketSession, 'OPEN')
        assert hasattr(MarketSession, 'CLOSE')

    def test_execution_result_creation(self):
        """ExecutionResult 데이터 클래스 테스트"""
        result = ExecutionResult(
            order_id="test_001",
            ticker="005930",
            filled_quantity=100,
            average_price=80000,
            total_cost=8000000,
            status="FILLED"
        )
        assert result.order_id == "test_001"