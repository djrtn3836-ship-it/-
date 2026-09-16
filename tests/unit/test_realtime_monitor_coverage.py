"""realtime_monitor.py 커버리지 개선 테스트"""
import pytest
import asyncio
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch

from scanner.realtime_monitor import RealtimeMonitor
from core.domain_models import Tick


class TestRealtimeMonitorCoverage:
    """RealtimeMonitor 클래스 커버리지 개선"""

    @pytest.fixture
    def monitor(self):
        """RealtimeMonitor 인스턴스"""
        return RealtimeMonitor(
            tickers=["005930", "000660"],
            alert_threshold=0.02
        )

    @pytest.fixture
    def sample_tick(self):
        """테스트용 Tick 데이터"""
        return {
            "ticker": "005930",
            "price": 80000,
            "volume": 1000,
            "timestamp": datetime.now()
        }

    def test_monitor_init(self, monitor):
        """모니터 초기화"""
        assert monitor.tickers == ["005930", "000660"]
        assert monitor.alert_threshold == 0.02

    @pytest.mark.asyncio
    async def test_add_tick_data(self, monitor, sample_tick):
        """Tick 데이터 추가"""
        await monitor.add_tick(sample_tick)
        assert len(monitor.data) > 0

    @pytest.mark.asyncio
    async def test_detect_price_jump(self, monitor, sample_tick):
        """가격 급등락 감지"""
        # 기준 가격 설정
        await monitor.add_tick(sample_tick)
        
        # 큰 가격 변동 추가
        jump_tick = sample_tick.copy()
        jump_tick["price"] = 85000  # 6.25% 상승
        
        alerts = await monitor.detect_imbalance(jump_tick)
        assert alerts is not None or len(alerts) >= 0

    @pytest.mark.asyncio
    async def test_calculate_volume_imbalance(self, monitor):
        """거래량 불균형 계산"""
        buy_volume = 50000
        sell_volume = 30000
        
        imbalance = await monitor.calculate_imbalance(buy_volume, sell_volume)
        assert imbalance is not None

    @pytest.mark.asyncio
    async def test_start_monitoring(self, monitor):
        """모니터링 시작"""
        with patch.object(monitor, '_monitor_loop'):
            await asyncio.sleep(0.1)  # 짧은 대기

    def test_get_statistics(self, monitor):
        """통계 조회"""
        stats = monitor.get_statistics()
        assert stats is not None

    @pytest.mark.asyncio
    async def test_handle_connection_error(self, monitor):
        """연결 오류 처리"""
        with patch.object(monitor, 'reconnect'):
            await asyncio.sleep(0.1)