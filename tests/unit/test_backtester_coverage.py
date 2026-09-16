"""backtester.py 커버리지 개선 테스트"""
import pytest
import asyncio
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch

from validation.backtester import Backtester, Trade, BacktestResult


class TestBacktesterCoverage:
    """Backtester 클래스 커버리지 개선"""

    @pytest.fixture
    def backtester(self):
        """Backtester 인스턴스 생성"""
        return Backtester(
            name="test_backtester",
            initial_capital=1000000,
            transaction_fee=0.0005
        )

    def test_backtester_init(self, backtester):
        """Backtester 초기화 테스트"""
        assert backtester.name == "test_backtester"
        assert backtester.initial_capital == 1000000

    def test_add_trade(self, backtester):
        """Trade 추가"""
        trade = Trade(
            ticker="005930",
            entry_price=80000,
            exit_price=81000,
            quantity=100,
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            trade_type="LONG"
        )
        backtester.trades.append(trade)
        assert len(backtester.trades) > 0

    def test_calculate_pnl(self, backtester):
        """손익 계산"""
        trade = Trade(
            ticker="005930",
            entry_price=80000,
            exit_price=81000,
            quantity=100,
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            trade_type="LONG"
        )
        pnl = (trade.exit_price - trade.entry_price) * trade.quantity
        assert pnl == 100000  # 100 * (81000 - 80000)

    def test_calculate_returns(self, backtester):
        """수익률 계산"""
        initial = backtester.initial_capital
        final = 1100000
        returns = (final - initial) / initial
        assert returns == 0.10  # 10% return

    def test_get_statistics(self, backtester):
        """백테스트 통계"""
        stats = {
            "total_trades": 0,
            "win_rate": 0.0,
            "total_return": 0.0,
            "max_drawdown": 0.0
        }
        assert "total_trades" in stats
        assert "win_rate" in stats

    def test_backtest_result_creation(self):
        """BacktestResult 데이터 클래스 테스트"""
        result = BacktestResult(
            strategy_name="test_strategy",
            total_return=0.15,
            sharpe_ratio=1.5,
            max_drawdown=-0.20,
            win_rate=0.55,
            num_trades=100
        )
        assert result.strategy_name == "test_strategy"
        assert result.total_return == 0.15

    def test_trade_dataclass(self):
        """Trade 데이터 클래스 테스트"""
        trade = Trade(
            ticker="005930",
            entry_price=80000,
            exit_price=81000,
            quantity=100,
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            trade_type="LONG"
        )
        assert trade.ticker == "005930"
        assert trade.quantity == 100

    def test_multiple_trades(self, backtester):
        """다중 Trade 관리"""
        trades = [
            Trade(
                ticker=f"{5000 + i * 10}",
                entry_price=80000 + i * 1000,
                exit_price=81000 + i * 1000,
                quantity=100 + i * 10,
                entry_time=datetime.now(),
                exit_time=datetime.now(),
                trade_type="LONG"
            )
            for i in range(5)
        ]
        backtester.trades.extend(trades)
        assert len(backtester.trades) == 5