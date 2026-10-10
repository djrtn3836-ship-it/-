# tests/conftest.py
"""
Pytest 공용 설정 및 Fixtures

🔴 P12-5 (2026-10-04): 테스트가 운영 데이터를 오염시키지 않도록 기록 경로를 임시 폴더로 격리한다.
   - 과거 사고: SignalPipeline을 태우는 테스트들이 logs/calibration_predictions.jsonl,
     logs/shadow_records.jsonl 에 직접 기록 → 운영 분석 데이터가 가짜 값으로 오염됨.
   - 모듈 임포트 시점에 경로 상수가 결정되므로 **conftest 최상단(임포트 전)** 에서 설정한다.
"""

import os
import tempfile
from pathlib import Path

_TEST_LOG_DIR = Path(tempfile.mkdtemp(prefix="sa_test_logs_"))

# setdefault가 아니라 강제 지정 — 테스트는 어떤 경우에도 운영 경로에 쓰지 않는다.
os.environ["CALIBRATION_LOG_DIR"] = str(_TEST_LOG_DIR)
os.environ["SHADOW_RECORDS_PATH"] = str(_TEST_LOG_DIR / "shadow_records.jsonl")
os.environ["ALERTS_AUDIT_PATH"] = str(_TEST_LOG_DIR / "alerts_audit.jsonl")
os.environ["READINESS_STATE_PATH"] = str(_TEST_LOG_DIR / "readiness_state.json")
# 🔴 2026-10-10: 튜닝 상태 파일 — 미격리 시 data/tuning_state.json 오염(가짜 임계값 커밋됨)
os.environ["TUNING_STATE_PATH"] = str(_TEST_LOG_DIR / "tuning_state.json")
# 로거/블랙박스도 임시 폴더로 (core/logger.py가 LOG_DIR를 지원)
os.environ["LOG_DIR"] = str(_TEST_LOG_DIR / "app_logs")
os.environ["BLACKBOX_DIR"] = str(_TEST_LOG_DIR / "blackbox")
os.environ["TRACE_DIR"] = str(_TEST_LOG_DIR / "trace")

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock
from core.config import get_config
from core.logger import setup_logger


# ============================================================
# Asyncio Fixture
# ============================================================
@pytest.fixture(scope='session')
def event_loop():
    """세션 전체에서 사용할 asyncio 이벤트 루프"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ============================================================
# Config Fixture
# ============================================================
@pytest.fixture
def config():
    """테스트용 설정 객체"""
    cfg = get_config()
    return cfg


# ============================================================
# Logger Fixture
# ============================================================
@pytest.fixture
def logger():
    """테스트용 로거"""
    return setup_logger("test")


# ============================================================
# Mock Fixtures
# ============================================================
@pytest.fixture
def mock_kiwoom():
    """Kiwoom 커넥터 Mock"""
    mock = AsyncMock()
    mock.register_realtime = AsyncMock(return_value=True)
    mock.unregister_realtime = AsyncMock(return_value=True)
    mock.get_price = AsyncMock(return_value=70000.0)
    return mock


@pytest.fixture
def mock_telegram():
    """Telegram 봇 Mock"""
    mock = Mock()
    mock.send_message = Mock(return_value=True)
    return mock


# ============================================================
# Test Data Fixture
# ============================================================
@pytest.fixture
def sample_market_data():
    """테스트용 시장 데이터"""
    return {
        "ticker": "005930",
        "name": "삼성전자",
        "price": 70000,
        "volume": 1000000,
        "change_ratio": 0.02,
        "timestamp": 1726521600.0
    }


@pytest.fixture
def sample_signal():
    """테스트용 신호 데이터"""
    return {
        "ticker": "005930",
        "action": "BUY",
        "score": 0.85,
        "confidence": 0.9,
        "timestamp": 1726521600.0
    }
