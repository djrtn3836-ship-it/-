# -*- coding: utf-8 -*-
"""
core/exception_handler.py - v1.1 (Session 40: mypy strict 적용 + 실제 버그 수정)

🔥 발견된 버그 수정: setup_global_exception_handler()가 원본 핸들러를
   sys.excepthook = sys_excepthook / loop.set_exception_handler(...) 호출
   이후에 캡처하고 있어, restore_exception_handler() 호출 시 커스텀 핸들러를
   자기 자신으로 재설정하는 무의미한 동작이 되고 진짜 원본으로는 절대
   복원되지 않던 문제. 원본 값을 핸들러 교체 "전"에 캡처하도록 순서 수정.
- 그 외 로직/동작 100% 무변경, 타입 힌트만 추가
"""

import asyncio
import sys
import traceback
import types
from collections.abc import Awaitable, Callable
from typing import Any, Dict, Optional

from core.logger import setup_logger

logger = setup_logger("exception_handler")

_send_alert_func: Optional[Callable[[str, str], Awaitable[None]]] = None


def set_alert_handler(func: Callable[[str, str], Awaitable[None]]) -> None:
    """Telegram 알림 함수를 전역에 등록"""
    global _send_alert_func
    _send_alert_func = func
    logger.info("✅ 전역 알림 핸들러 등록 완료")


def _send_alert_sync(error_msg: str, error_detail: str = "") -> None:
    """동기 컨텍스트에서 알림을 보내기 위한 래퍼 (이벤트 루프에서 실행)"""
    if _send_alert_func is None:
        return

    try:
        asyncio.get_running_loop()
        asyncio.create_task(_send_alert_func(error_msg, error_detail))
    except RuntimeError:
        try:
            asyncio.run(_send_alert_func(error_msg, error_detail))
        except Exception:
            pass
    except Exception:
        pass


def global_exception_handler(loop: asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
    """asyncio 이벤트 루프의 전역 예외 핸들러"""
    exception: Optional[BaseException] = context.get("exception")
    message: str = str(context.get("message", "알 수 없는 오류"))
    future: Any = context.get("future")

    logger.error(f"🚨 [ASYNCIO] {message}")
    if exception:
        logger.error(f"   예외: {type(exception).__name__}: {exception}")
        logger.error(traceback.format_exc())

    if exception:
        _send_alert_sync(f"🚨 [ASYNCIO] {message}", f"{type(exception).__name__}: {str(exception)[:200]}")
    else:
        _send_alert_sync(f"🚨 [ASYNCIO] {message}", "")

    if future and not future.done():
        future.cancel()


def setup_global_exception_handler() -> Dict[str, Any]:
    """전역 예외 핸들러 설정.

    🔥 Session 40 버그 수정: 원본 핸들러를 교체하기 전에 먼저 캡처합니다.
    (기존 코드는 교체 후에 캡처하여 restore()가 무의미한 no-op이 되던 버그가 있었음)
    """
    loop = asyncio.get_event_loop()

    # 1. 원본 핸들러를 먼저 캡처 (교체 전!)
    original_loop_handler = loop.get_exception_handler()
    original_excepthook = sys.excepthook

    # 2. 이제 새 핸들러로 교체
    loop.set_exception_handler(global_exception_handler)

    def sys_excepthook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: Optional[types.TracebackType],
    ) -> None:
        logger.error(f"🚨 [SYSTEM] {exc_type.__name__}: {exc_value}")
        if exc_traceback:
            logger.error("".join(traceback.format_tb(exc_traceback)))
        _send_alert_sync(f"🚨 [SYSTEM] {exc_type.__name__}", str(exc_value)[:200])

    sys.excepthook = sys_excepthook

    logger.info("✅ 전역 예외 핸들러 설정 완료 (asyncio + sys)")

    # 3. 진짜 원본 값을 반환 (복원용)
    return {
        "original_excepthook": original_excepthook,
        "original_loop_handler": original_loop_handler,
    }


def restore_exception_handler(original_handlers: Dict[str, Any]) -> None:
    """원래 예외 핸들러 복원 (종료 시). Session 40 버그 수정으로 이제 실제로 동작함."""
    if "original_excepthook" in original_handlers:
        sys.excepthook = original_handlers["original_excepthook"]

    if "original_loop_handler" in original_handlers:
        try:
            loop = asyncio.get_event_loop()
            loop.set_exception_handler(original_handlers["original_loop_handler"])
        except RuntimeError:
            pass
