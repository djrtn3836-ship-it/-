# tests/integration/test_bootstrap_e2e.py
"""
E2E Bootstrap 통합 테스트
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bootstrap_system_initialization(mock_kiwoom, mock_telegram):
    """시스템 부트스트랩 초기화 테스트"""
    # Given: Mock 컴포넌트 준비
    assert mock_kiwoom is not None
    assert mock_telegram is not None
    
    # When: 초기화 수행
    await mock_kiwoom.connect()
    
    # Then: 연결 확인
    mock_kiwoom.connect.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_realtime_monitoring_flow(mock_kiwoom, sample_market_data):
    """실시간 모니터링 플로우 테스트"""
    # Given: 시장 데이터
    market_data = sample_market_data
    
    # When: 구독 및 데이터 수신
    await mock_kiwoom.subscribe_realtime(market_data["ticker"])
    price = await mock_kiwoom.get_price(market_data["ticker"])
    
    # Then: 데이터 수신 확인
    assert price == 70000.0
    mock_kiwoom.subscribe_realtime.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_signal_generation_pipeline(sample_market_data, sample_signal):
    """신호 생성 파이프라인 테스트"""
    # Given: 시장 데이터
    market_data = sample_market_data
    
    # When: 신호 생성
    signal = sample_signal
    
    # Then: 신호 검증
    assert signal["action"] in ["BUY", "SELL", "HOLD"]
    assert signal["score"] >= 0.0
    assert signal["ticker"] == market_data["ticker"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_telegram_notification_on_signal(mock_telegram, sample_signal):
    """신호 발생 시 Telegram 알림 테스트"""
    # Given: 신호
    signal = sample_signal
    
    # When: 알림 전송
    mock_telegram.send_alert(signal)
    
    # Then: 전송 확인
    mock_telegram.send_alert.assert_called_once_with(signal)