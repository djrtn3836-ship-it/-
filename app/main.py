#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/main.py - V10 Application Entry Point (유일한 진입점)

실행 방법:
    python app/main.py

기능:
    - Windows UTF-8 인코딩 강제 설정
    - asyncio.run() 단일 진입점
    - SIGINT / SIGTERM 그레이스풀 셧다운
    - 비거래일 자동 감지 및 조기 종료
    - Bootstrapper 위임 (모든 실제 로직은 app/bootstrap.py)

V10 아키텍처에서 scanner_main.py 대신 이 파일을 사용합니다.
"""

# ============================================================
# Windows 콘솔 UTF-8 인코딩 강제 설정 (한글 깨짐 방지)
# 모든 import 이전에 실행
# ============================================================
import sys
import os

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

import asyncio
import signal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from observability.tracer import get_tracer
from observability.trace_id import bind_trace_id, reset_trace_id, new_trace_id
from core.holiday_utils import is_trading_day

trace = get_tracer(__name__)

_shutdown_event: asyncio.Event = None


def _signal_handler(sig, frame) -> None:
    """SIGINT / SIGTERM 수신 시 그레이스풀 셧다운 이벤트 설정."""
    print(f"\nSignal {sig} received, shutting down gracefully...")
    trace.info(f"Signal {sig} received → shutdown event set")
    if _shutdown_event is not None:
        _shutdown_event.set()


async def main() -> None:
    """V10 시스템 메인 코루틴."""
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    token = bind_trace_id(f"SYS-{new_trace_id()}")
    try:
        # ─── 비거래일 조기 종료 ───────────────────────────────────
        if not is_trading_day():
            from core.blackbox_logger import log_event
            from core.debug_tower import debug_tower
            import datetime
            log_event("NON_TRADING_DAY", {"date": datetime.date.today().isoformat()})
            debug_tower.log("SYSTEM", "NON_TRADING_DAY", {})
            print("📅 오늘은 비거래일입니다. 시스템을 종료합니다.")
            trace.info("Non-trading day → exit")
            return

        trace.info("Starting V10 system (app/main.py → app/bootstrap.py)")
        from app.bootstrap import Bootstrapper

        boot = Bootstrapper()
        # bootstrap() 내부에서 run_main_loop() + shutdown() 까지 완전 처리
        await boot.bootstrap(shutdown_event=_shutdown_event)
        trace.info("V10 system shutdown complete")

    except KeyboardInterrupt:
        trace.info("Interrupted by user (Ctrl+C)")
        print("\nInterrupted by user")
    except SystemExit:
        # validate_env(), manage_pid() 에서 sys.exit() 호출 시 정상 처리
        raise
    except Exception as e:
        trace.error("System error during execution", exc=e)
        print(f"\nSystem error: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        reset_trace_id(token)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except SystemExit:
        pass  # validate_env 등의 정상 종료
    except Exception as e:
        print(f"\nSystem error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
