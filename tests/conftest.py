"""
tests/conftest.py - pytest 공통 픽스처 및 설정
"""

import sys
import os
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from typing import Dict, List


@pytest.fixture
def sample_orderbook() -> Dict:
    """샘플 호가 데이터"""
    return {
        'bids': [
            (82000, 1000),
            (81500, 500),
            (81000, 200),
        ],
        'asks': [
            (83000, 800),
            (83500, 600),
            (84000, 300),
        ]
    }


@pytest.fixture
def sample_tech_data() -> Dict:
    """샘플 기술적 지표"""
    return {
        'ema5': 82500.0,
        'ema20': 81800.0,
        'ema60': 80500.0,
        'rsi': 65.0,
        'volume_ratio': 1.8,
        'avg_volume': 1500000,
        'current_price': 83000.0,
    }


@pytest.fixture
def sample_stock_data() -> Dict:
    """샘플 종목 데이터"""
    return {
        'ticker': '005930',
        'price': 83000.0,
        'entry_price': 82000.0,
        'imbalance': 0.65,
        'regime': 'Bull',
        'momentum': 0.025,
        'volume': 1500000,
        'atr': 1200.0,
        'high_52w': 90000.0,
        'low_52w': 70000.0,
        'bb_upper': 85000.0,
        'bb_lower': 79000.0,
        'adx': 32.0,
        'tech_data': {
            'ema5': 82500.0,
            'ema20': 81800.0,
            'ema60': 80500.0,
            'rsi': 65.0,
            'volume_ratio': 1.8,
            'avg_volume': 1500000,
        }
    }


@pytest.fixture
def sample_returns() -> List[float]:
    """샘플 수익률 데이터 (252일)"""
    import random
    random.seed(42)
    returns = []
    for i in range(300):
        returns.append(random.gauss(0.0005, 0.015))
    return returns