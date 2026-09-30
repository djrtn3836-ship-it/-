# -*- coding: utf-8 -*-
"""
data/stock_universe.py — DEPRECATED (하위 호환 shim)

V10부터 유니버스 정본은 `infrastructure/market_data/universe_provider.py` 입니다.
이 모듈은 기존 import 경로(`from data.stock_universe import get_universe`)를
보존하기 위해 정본의 심볼을 재노출(re-export)만 합니다.

- 중복 정의(하드코딩 238종목 등) 제거 → 단일 소스 원칙
- 신규 코드는 `infrastructure.market_data.universe_provider` 를 직접 사용하세요.
"""

from infrastructure.market_data.universe_provider import (  # noqa: F401
    CSV_PATH,
    FALLBACK_500,
    StockInfo,
    StockUniverse,
    get_last_source,
    get_universe,
    is_fallback,
    validate_universe,
)

__all__ = [
    "CSV_PATH",
    "FALLBACK_500",
    "StockInfo",
    "StockUniverse",
    "get_last_source",
    "get_universe",
    "is_fallback",
    "validate_universe",
]
