# tests/unit/test_monitor.py
\"\"\"
RealtimeMonitor 단위 테스트
\"\"\"

import pytest
from scanner.realtime_monitor import RealtimeMonitor


class TestRealtimeMonitor:
    \"\"\"RealtimeMonitor 테스트\"\"\"

    @pytest.mark.asyncio
    async def test_init(self, mock_kiwoom):
        \"\"\"모니터 초기화 테스트\"\"\"
        monitor = RealtimeMonitor(mock_kiwoom)
        assert monitor is not None
        assert monitor.max_subscriptions > 0
        assert monitor.is_running() == False

    @pytest.mark.asyncio
    async def test_on_data_handler(self, mock_kiwoom, sample_market_data):
        \"\"\"데이터 핸들러 테스트\"\"\"
        monitor = RealtimeMonitor(mock_kiwoom)
        # 데이터 처리 (예외 없음을 확인)
        monitor._on_data(sample_market_data)
        assert monitor.get_latest_price(sample_market_data['ticker']) == sample_market_data['price']

    def test_calculate_imbalance(self, mock_kiwoom):
        \"\"\"호가 불균형 계산 테스트\"\"\"
        monitor = RealtimeMonitor(mock_kiwoom)
        bids = [(70000, 1000), (69900, 800)]
        asks = [(70100, 500), (70200, 300)]
        
        imbalance, pressure = monitor._calculate_imbalance(bids, asks)
        assert 0.0 <= imbalance <= 1.0
        assert isinstance(pressure, str)
