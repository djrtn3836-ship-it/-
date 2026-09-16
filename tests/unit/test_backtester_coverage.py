"""backtester.py 커버리지 개선 테스트"""
import pytest
import asyncio
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch

from validation.backtester import Backtester
from core.domain_models import Signal, Position, TradeResult


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

    @pytest.fixture
    def sample_signal(self):
        """테스트용 Signal 객체"""
        return Signal(
            ticker="005930",
            price=80000,
            signal_type="BUY",
            confidence=0.95,
            timestamp=datetime.now()
        )

    def test_backtester_init(self, backtester):
        """Backtester 초기화 테스트"""
        assert backtester.name == "test_backtester"
        assert backtester.initial_capital == 1000000
        assert backtester.transaction_fee == 0.0005
        assert backtester.current_portfolio_value == 1000000

    @pytest.mark.asyncio
    async def test_execute_signal_buy(self, backtester, sample_signal):
        """BUY 신호 실행"""
        result = await backtester.execute_signal(sample_signal)
        assert result is not None

    @pytest.mark.asyncio
    async def test_execute_signal_sell(self, backtester, sample_signal):
        """SELL 신호 실행"""
        sell_signal = Signal(
            ticker="005930",
            price=81000,
            signal_type="SELL",
            confidence=0.90,
            timestamp=datetime.now()
        )
        result = await backtester.execute_signal(sell_signal)
        assert result is not None

    def test_calculate_returns(self, backtester):
        """수익률 계산"""
        backtester.current_portfolio_value = 1100000
        returns = backtester.calculate_returns()
        assert returns == 0.10  # 10% return

    def test_get_statistics(self, backtester):
        """백테스트 통계"""
        stats = backtester.get_statistics()
        assert "sharpe_ratio" in stats or "total_return" in stats

    @pytest.mark.asyncio
    async def test_run_backtest(self, backtester, sample_signal):
        """전체 백테스트 실행"""
        signals = [sample_signal]
        result = await backtester.run_backtest(signals)
        assert result is not None