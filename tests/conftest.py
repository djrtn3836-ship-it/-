# tests/conftest.py
\"\"\"
Pytest 공용 설정 및 Fixtures
\"\"\"

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from core.config import get_config
from core.logger import setup_logger

# ============================================================
# Asyncio Fixture
# ============================================================
@pytest.fixture(scope='session')
def event_loop():
    \"\"\"세션 전체에서 사용할 asyncio 이벤트 루프\"\"\"
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ============================================================
# Config Fixture
# ============================================================
@pytest.fixture
def config():
    \"\"\"테스트용 설정 객체\"\"\"
    cfg = get_config()
    return cfg


# ============================================================
# Logger Fixture
# ============================================================
@pytest.fixture
def logger():
    \"\"\"테스트용 로거\"\"\"
    return setup_logger("test")


# ============================================================
# Mock Fixtures
# ============================================================
@pytest.fixture
def mock_kiwoom():
    \"\"\"Kiwoom 커넥터 Mock\"\"\"
    mock = AsyncMock()
    mock.register_realtime = AsyncMock(return_value=True)
    mock.unregister_realtime = AsyncMock(return_value=True)
    mock.get_price = AsyncMock(return_value=70000.0)
    return mock


@pytest.fixture
def mock_telegram():
    \"\"\"Telegram 봇 Mock\"\"\"
    mock = Mock()
    mock.send_message = Mock(return_value=True)
    return mock


# ============================================================
# Test Data Fixture
# ============================================================
@pytest.fixture
def sample_market_data():
    \"\"\"테스트용 시장 데이터\"\"\"
    return {
        'ticker': '005930',
        'name': '삼성전자',
        'price': 70000,
        'volume': 1000000,
        'change_ratio': 0.02,
        'timestamp': 1726521600.0
    }


@pytest.fixture
def sample_signal():
    \"\"\"테스트용 신호 데이터\"\"\"
    return {
        'ticker': '005930',
        'action': 'BUY',
        'score': 0.85,
        'confidence': 0.9,
        'timestamp': 1726521600.0
    }
