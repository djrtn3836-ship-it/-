# -*- coding: utf-8 -*-
"""infrastructure/market_data/mock_kiwoom_connector.py - 안전모드용 모의 키움 커넥터.

TEST_MODE에서 실제 키움 API(인증/WebSocket)에 접속하지 않고도
부팅 시퀀스와 모니터 구독 흐름을 검증할 수 있게 한다.

- 네트워크/자격증명 불필요
- 실제 데이터는 흐르지 않음(구독 요청만 기록)
"""

from __future__ import annotations

from typing import Any, Optional

from core.logger import setup_logger

logger = setup_logger("mock_kiwoom")


class MockKiwoomConnector:
    """실제 통신 없는 모의 커넥터 (TEST_MODE 전용)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._connected: bool = False
        self._subscribed: list[str] = []
        self._registered_handler: Optional[Any] = None

    # ── 연결 ────────────────────────────────────────────────
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        if not self._connected:
            self._connected = True
            logger.info("🧪 [SAFE MODE] 모의 키움 연결 (실제 API 미사용)")
        return None

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("🧪 [SAFE MODE] 모의 키움 연결 해제")
        return None

    async def wait_until_ready(self, timeout: float = 10.0) -> bool:
        return True

    # ── 실시간 구독 ─────────────────────────────────────────
    async def register_realtime(
        self,
        ticker: str,
        handler: Optional[Any] = None,
        types: Optional[list[str]] = None,
    ) -> bool:
        if handler is not None:
            self._registered_handler = handler
        if ticker not in self._subscribed:
            self._subscribed.append(ticker)
        return True

    async def unregister_realtime(self, ticker: str) -> bool:
        if ticker in self._subscribed:
            self._subscribed.remove(ticker)
        return True

    async def resubscribe_all(self) -> None:
        logger.info(f"🧪 [SAFE MODE] 모의 재구독: {len(self._subscribed)}개")
        return None

    # ── 조회 ────────────────────────────────────────────────
    def get_subscribed_count(self) -> int:
        return len(self._subscribed)

    def get_subscribed_tickers(self) -> list[str]:
        return list(self._subscribed)

    async def get_price(self, ticker: str) -> float:
        return 0.0

    async def get_ohlcv(self, ticker: str, *args: Any, **kwargs: Any) -> list[Any]:
        return []
