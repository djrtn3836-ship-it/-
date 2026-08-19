"""
core/exception_handler.py - v1.0 FINAL (전역 예외 핸들러)
- 시스템 전체에서 발생하는 모든 예외를 중앙에서 처리
- 예외 발생 시 로깅 + Telegram 알림 통합
- sys.excepthook과 asyncio.get_event_loop().set_exception_handler() 연동
"""

import sys
import asyncio
import traceback
from typing import Optional, Callable, Awaitable

from core.logger import setup_logger

logger = setup_logger("exception_handler")

# 전역 알림 함수 (scanner_main에서 설정)
_send_alert_func: Optional[Callable[[str, str], Awaitable[None]]] = None


def set_alert_handler(func: Callable[[str, str], Awaitable[None]]):
    """Telegram 알림 함수를 전역에 등록"""
    global _send_alert_func
    _send_alert_func = func
    logger.info("✅ 전역 알림 핸들러 등록 완료")


def _send_alert_sync(error_msg: str, error_detail: str = ""):
    """동기 컨텍스트에서 알림을 보내기 위한 래퍼 (이벤트 루프에서 실행)"""
    if _send_alert_func is None:
        return

    try:
        loop = asyncio.get_running_loop()
        # 이미 이벤트 루프가 실행 중이면 create_task로 실행
        asyncio.create_task(_send_alert_func(error_msg, error_detail))
    except RuntimeError:
        # 이벤트 루프가 없으면 새로 만들어서 실행 (비동기 함수를 동기로 실행)
        try:
            asyncio.run(_send_alert_func(error_msg, error_detail))
        except:
            pass
    except Exception:
        pass


def global_exception_handler(loop, context):
    """
    asyncio 이벤트 루프의 전역 예외 핸들러
    """
    exception = context.get('exception')
    message = context.get('message', '알 수 없는 오류')
    future = context.get('future')

    # 로깅
    logger.error(f"🚨 [ASYNCIO] {message}")
    if exception:
        logger.error(f"   예외: {type(exception).__name__}: {exception}")
        logger.error(traceback.format_exc())

    # Telegram 알림
    if exception:
        _send_alert_sync(
            f"🚨 [ASYNCIO] {message}",
            f"{type(exception).__name__}: {str(exception)[:200]}"
        )
    else:
        _send_alert_sync(f"🚨 [ASYNCIO] {message}", "")

    # Future가 있으면 취소
    if future and not future.done():
        future.cancel()


def setup_global_exception_handler():
    """전역 예외 핸들러 설정"""
    # 1. asyncio 이벤트 루프 핸들러
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(global_exception_handler)

    # 2. sys.excepthook (동기 예외 처리)
    def sys_excepthook(exc_type, exc_value, exc_traceback):
        logger.error(f"🚨 [SYSTEM] {exc_type.__name__}: {exc_value}")
        logger.error(''.join(traceback.format_tb(exc_traceback)))
        _send_alert_sync(
            f"🚨 [SYSTEM] {exc_type.__name__}",
            str(exc_value)[:200]
        )

    sys.excepthook = sys_excepthook

    logger.info("✅ 전역 예외 핸들러 설정 완료 (asyncio + sys)")

    # 기존 핸들러 반환 (복원용)
    return {
        "original_excepthook": sys.excepthook,
        "original_loop_handler": loop.get_exception_handler()
    }


def restore_exception_handler(original_handlers: dict):
    """원래 예외 핸들러 복원 (종료 시)"""
    if "original_excepthook" in original_handlers:
        sys.excepthook = original_handlers["original_excepthook"]

    if "original_loop_handler" in original_handlers:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(original_handlers["original_loop_handler"])