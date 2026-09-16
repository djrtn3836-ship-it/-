# tests/integration/conftest.py
"""
Integration 테스트 공용 Fixtures
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path


@pytest.fixture(scope="session")
def event_loop():
    """세션 전체 asyncio 이벤트 루프"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_kiwoom():
    """Mock Kiwoom 커넥터"""
    mock = AsyncMock()
    mock.connect = AsyncMock(return_value=True)
    mock.disconnect = AsyncMock(return_value=True)
    mock.get_price = AsyncMock(return_value=70000.0)
    mock.subscribe_realtime = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def mock_telegram():
    """Mock Telegram 봇"""
    mock = MagicMock()
    mock.send_message = MagicMock(return_value=True)
    mock.send_alert = MagicMock(return_value=True)
    return mock


@pytest.fixture
def mock_redis():
    """Mock Redis 캐시"""
    mock = MagicMock()
    mock.get = MagicMock(return_value=None)
    mock.set = MagicMock(return_value=True)
    mock.delete = MagicMock(return_value=True)
    return mock


@pytest.fixture
def sample_market_data():
    """테스트용 시장 데이터"""
    return {
        "ticker": "005930",
        "name": "삼성전자",
        "price": 70000,
        "volume": 1000000,
        "timestamp": 1234567890.0,
    }


@pytest.fixture
def sample_signal():
    """테스트용 신호"""
    return {
        "ticker": "005930",
        "action": "BUY",
        "score": 0.85,
        "timestamp": 1234567890.0,
    }