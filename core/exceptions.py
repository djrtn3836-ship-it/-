# -*- coding: utf-8 -*-
"""
core/exceptions.py - v2.1 (Session 40: mypy strict 적용)
- handle_exceptions 데코레이터는 Session 32에서 검증된 observability/tracer.py의
  traced() 패턴(Callable[..., Any] + 분기별 정의)을 그대로 적용 (TypeVar/cast 불필요)
- 모든 __init__에 -> None 반환 타입 추가
- 로직/동작 100% 무변경
"""

import asyncio
import functools
import logging
import traceback
from collections.abc import Awaitable, Callable
from typing import Any, Optional


class KiwoomError(Exception):
    """키움 API 관련 기본 예외"""

    def __init__(self, message: str, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.code: Optional[str] = code


class KiwoomAuthError(KiwoomError):
    """인증 관련 예외 (토큰, IP, 권한)"""
    pass


class KiwoomWebSocketError(KiwoomError):
    """WebSocket 연결 관련 예외"""
    pass


class KiwoomRateLimitError(KiwoomError):
    """Rate Limit 초과 예외"""
    pass


class KiwoomDataError(KiwoomError):
    """데이터 처리 관련 예외"""
    pass


class KiwoomTokenExpiredError(KiwoomAuthError):
    """토큰 만료 예외"""

    def __init__(self, message: str = "Access Token이 만료되었습니다") -> None:
        super().__init__(message, code="100013")


class ConfigError(Exception):
    """설정 관련 예외"""
    pass


class DatabaseError(Exception):
    """데이터베이스 관련 예외"""
    pass


class TelegramError(Exception):
    """텔레그램 관련 예외"""
    pass


class WebSocketConnectionError(Exception):
    """WebSocket 연결 실패 예외"""
    pass


class WebSocketAuthError(Exception):
    """WebSocket 인증 실패 예외"""
    pass


class DataCollectionError(Exception):
    """데이터 수집 실패 예외 (뉴스, DART, 거시)"""

    def __init__(self, source: str, message: str) -> None:
        self.source: str = source
        self.message: str = message
        super().__init__(f"[{source}] {message}")


class StrategyExecutionError(Exception):
    """전략 실행 중 오류"""

    def __init__(self, strategy_name: str, message: str) -> None:
        self.strategy_name: str = strategy_name
        self.message: str = message
        super().__init__(f"[{strategy_name}] {message}")


def handle_exceptions(
    logger: logging.Logger,
    send_alert_func: Optional[Callable[[str, str], Awaitable[None]]] = None,
    reraise: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    예외 발생 시 로깅 + Telegram 알림을 자동으로 처리하는 데코레이터

    Args:
        logger: 로거 인스턴스
        send_alert_func: Telegram 알림 함수 (async)
        reraise: 예외를 다시 발생시킬지 여부
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    logger.error(f"❌ {func.__name__} 실행 중 오류: {e}")
                    logger.error(traceback.format_exc())

                    if send_alert_func:
                        try:
                            await send_alert_func(
                                f"🚨 {func.__name__} 오류", f"{type(e).__name__}: {str(e)[:200]}"
                            )
                        except Exception as alert_e:
                            logger.error(f"⚠️ 알림 전송 실패: {alert_e}")

                    if reraise:
                        raise
                    return None
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    logger.error(f"❌ {func.__name__} 실행 중 오류: {e}")
                    logger.error(traceback.format_exc())
                    if send_alert_func:
                        try:
                            # 동기 함수에서 async 알림 호출은 복잡하므로, 여기서는 생략 (원본 유지)
                            pass
                        except Exception:
                            pass
                    if reraise:
                        raise
                    return None
            return sync_wrapper

    return decorator
