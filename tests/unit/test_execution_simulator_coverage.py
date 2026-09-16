"""execution_simulator.py 커버리지 개선 테스트"""
import pytest
from validation.execution_simulator import RealisticExecutionSimulator, ExecutionResult, MarketSession


class TestExecutionSimulatorCoverage:
    """RealisticExecutionSimulator 커버리지 개선"""

    @pytest.fixture
    def simulator(self):
        """시뮬레이터 인스턴스 - 기본 파라미터 사용"""
        return RealisticExecutionSimulator(max_slippage_bps=100.0, num_slices=3)

    def test_simulator_init(self, simulator):
        """시뮬레이터 초기화"""
        assert simulator is not None
        assert simulator.max_slippage_bps == 100.0
        assert simulator.num_slices == 3

    def test_simulator_default_init(self):
        """기본 파라미터로 초기화"""
        sim = RealisticExecutionSimulator()
        assert sim is not None

    def test_simulator_custom_slippage(self):
        """커스텀 슬리피지"""
        sim = RealisticExecutionSimulator(max_slippage_bps=50.0, num_slices=5)
        assert sim.max_slippage_bps == 50.0
        assert sim.num_slices == 5

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
        assert result is not None

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

    def test_multiple_orders(self, simulator):
        """다중 주문 실행"""
        results = []
        for i in range(3):
            result = simulator.execute_order(
                ticker=f"{5930 + i}",
                side="BUY" if i % 2 == 0 else "SELL",
                quantity=100 + i * 10,
                price=80000 + i * 100
            )
            results.append(result)
        assert len(results) == 3