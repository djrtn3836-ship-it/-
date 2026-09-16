"""execution_simulator.py 커버리지 개선 테스트"""
import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock

from validation.execution_simulator import ExecutionSimulator
from core.domain_models import Order


class TestExecutionSimulatorCoverage:
    """ExecutionSimulator 커버리지 개선"""

    @pytest.fixture
    def simulator(self):
        """시뮬레이터 인스턴스"""
        return ExecutionSimulator(
            name="test_simulator",
            latency_ms=10
        )

    @pytest.fixture
    def buy_order(self):
        """BUY 주문"""
        return Order(
            order_id="order_001",
            ticker="005930",
            side="BUY",
            quantity=100,
            price=80000,
            timestamp=datetime.now()
        )

    @pytest.fixture
    def sell_order(self):
        """SELL 주문"""
        return Order(
            order_id="order_002",
            ticker="005930",
            side="SELL",
            quantity=100,
            price=81000,
            timestamp=datetime.now()
        )

    def test_simulator_init(self, simulator):
        """시뮬레이터 초기화"""
        assert simulator.name == "test_simulator"
        assert simulator.latency_ms == 10

    def test_execute_buy_order(self, simulator, buy_order):
        """BUY 주문 실행"""
        result = simulator.execute_order(buy_order)
        assert result is not None
        assert result["status"] in ["FILLED", "PARTIAL"]

    def test_execute_sell_order(self, simulator, sell_order):
        """SELL 주문 실행"""
        result = simulator.execute_order(sell_order)
        assert result is not None

    def test_simulate_partial_fill(self, simulator, buy_order):
        """부분 체결 시뮬레이션"""
        buy_order.quantity = 1000
        result = simulator.execute_order(buy_order)
        assert result["filled_quantity"] <= buy_order.quantity

    def test_simulate_slippage(self, simulator, buy_order):
        """슬리피지 시뮬레이션"""
        with_slippage = simulator.apply_slippage(buy_order.price, "BUY", 0.001)
        assert with_slippage > buy_order.price  # BUY는 위로

    def test_calculate_transaction_cost(self, simulator):
        """거래 비용 계산"""
        cost = simulator.calculate_transaction_cost(100, 80000, 0.0005)
        assert cost == 4.0  # 100 * 80000 * 0.0005

    def test_get_execution_report(self, simulator, buy_order):
        """실행 리포트"""
        simulator.execute_order(buy_order)
        report = simulator.get_execution_report()
        assert report is not None

    def test_reset_simulator(self, simulator):
        """시뮬레이터 리셋"""
        simulator.reset()
        assert len(simulator.executed_orders) == 0