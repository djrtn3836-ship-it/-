"""
core/exceptions.py - v2.0 FINAL (공통 예외 처리 표준화)
- 모든 커스텀 예외 클래스 정의
- 예외 발생 시 자동 로깅 및 Telegram 알림을 위한 데코레이터 제공
"""


class KiwoomError(Exception):
    """키움 API 관련 기본 예외"""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


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

    def __init__(self, message: str = "Access Token이 만료되었습니다"):
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

    def __init__(self, source: str, message: str):
        self.source = source
        self.message = message
        super().__init__(f"[{source}] {message}")


class StrategyExecutionError(Exception):
    """전략 실행 중 오류"""

    def __init__(self, strategy_name: str, message: str):
        self.strategy_name = strategy_name
        self.message = message
        super().__init__(f"[{strategy_name}] {message}")


# ============================================================
# 예외 처리 데코레이터 (v2.0 신규)
# ============================================================
def handle_exceptions(logger, send_alert_func=None, reraise: bool = False):
    """
    예외 발생 시 로깅 + Telegram 알림을 자동으로 처리하는 데코레이터

    Args:
        logger: 로거 인스턴스
        send_alert_func: Telegram 알림 함수 (async)
        reraise: 예외를 다시 발생시킬지 여부
    """
    import asyncio
    import functools
    import traceback

    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                # 로깅
                logger.error(f"❌ {func.__name__} 실행 중 오류: {e}")
                logger.error(traceback.format_exc())

                # Telegram 알림
                if send_alert_func:
                    try:
                        await send_alert_func(f"🚨 {func.__name__} 오류", f"{type(e).__name__}: {str(e)[:200]}")
                    except Exception as alert_e:
                        logger.error(f"⚠️ 알림 전송 실패: {alert_e}")

                if reraise:
                    raise
                return None

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"❌ {func.__name__} 실행 중 오류: {e}")
                logger.error(traceback.format_exc())
                if send_alert_func:
                    try:
                        # 동기 함수에서 async 알림 호출은 복잡하므로, 여기서는 생략
                        # (scanner_main의 send_error_alert를 직접 호출하는 방식으로 대체)
                        pass
                    except:
                        pass
                if reraise:
                    raise
                return None

        # 함수가 async인지 확인
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
