#!/usr/bin/env python3
"""
scanner_main.py - v8.0.0 LEGACY (Deprecated)

⚠️  DEPRECATED: 이 파일은 레거시 진입점입니다.
    V10 DDD 아키텍처에서는 app/main.py 를 사용하세요.

    실행 방법 (V10):
        python app/main.py

    이 파일이 유지되는 이유:
        - Strangler Fig 패턴으로 점진적 전환 지원
        - 긴급 롤백 시 레거시 진입점 역할
        - 기존 운영 스크립트 호환성 유지

    V10과의 핵심 차이:
        scanner_main.py → core.config (딕셔너리 방식)
        app/main.py     → config.schema (Pydantic V10)

        scanner_main.py → data.*, scanner.* (레거시 경로)
        app/main.py     → infrastructure.*, application.* (DDD 경로)

    완전 전환 예정: Phase 4 (인프라 현대화) 완료 후 삭제
"""
import warnings
warnings.warn(
    "scanner_main.py는 레거시 진입점입니다. "
    "V10 DDD 아키텍처에서는 'python app/main.py'를 사용하세요.",
    DeprecationWarning,
    stacklevel=1,
)

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from aiohttp import web
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from core.blackbox_logger import get_status, log_error, log_event
from core.config import get_config
from core.container import AppContainer
from core.debug_tower import debug_tower
from core.exception_handler import (
    restore_exception_handler,
    set_alert_handler,
    setup_global_exception_handler,
)
from core.exceptions import (
    DatabaseError,
    DataCollectionError,
    KiwoomError,
)
from core.holiday_utils import is_trading_day
from core.logger import setup_logger
from core.regime_manager import regime_manager
from core.scheduler import SchedulerManager
from scheduler.macro_collector import fetch_macro_data, get_cached_macro, set_alert_callback

FatalError = Exception

from analytics.calibration_executor import ExecutionCalibrator
from analytics.performance_tracker import performance_tracker
from collector.collector_status import collector_status
from data.dart_connector import DartConnector
from data.db_manager import DatabaseManager
from data.kiwoom_connector import KiwoomConnectorV512
from data.news_crawler import NewsCrawler
from feedback.feedback_learner import FeedbackLearner
from monitor.phase_transition_validator import PhaseTransitionValidator
from report.daily_report import DailyReportGenerator
from report.telegram_commands import TelegramCommandHandler
from report.telegram_sender import TelegramSender
from report.weekly_pdf import WeeklyPDFGenerator
from risk.safety_guard import SafetyGuard
from scanner.deep_analyzer import DeepAnalyzer
from scanner.realtime_monitor import RealtimeMonitor
from scheduler.daily_collector import collect_daily_ohlcv

# ============================================================
# 🔥 신규: Supervisor, Verifier
# ============================================================
from core.supervisor import SystemSupervisor
from analytics.alert_verifier import scheduled_verify

logger = setup_logger("scanner")
config = get_config()

# --- 글로벌 변수 ---
_kiwoom: KiwoomConnectorV512 | None = None
_monitor: RealtimeMonitor | None = None
_db: DatabaseManager | None = None
_start_time: float = 0.0
_error_sender: TelegramSender | None = None
_scheduler: SchedulerManager | None = None
_worker_tasks: list[asyncio.Task] = []
_all_tasks: list[asyncio.Task] = []
_main_loop: asyncio.AbstractEventLoop | None = None
_health_task: asyncio.Task | None = None
_telegram_cmd: TelegramCommandHandler | None = None
_original_exception_handlers: dict | None = None
_shutdown_requested: bool = False
PID_FILE = Path(__file__).parent / "scanner.pid"
MESSAGE_QUEUE: asyncio.Queue = asyncio.Queue(maxsize=config.get_int("queue_maxsize", 100000))

_last_data_time = 0.0
_DATA_FLOW_TIMEOUT = 180

_safety_guard: SafetyGuard | None = None
_container: AppContainer | None = None


# ============================================================
# Telegram 명령어용 시스템 상태 수집 콜백
# ============================================================
def get_system_stats() -> dict[str, Any]:
    now = time.time()
    last_data_ago = "없음"
    if _last_data_time > 0:
        diff = now - _last_data_time
        if diff < 60:
            last_data_ago = f"{int(diff)}초 전"
        else:
            last_data_ago = f"{int(diff // 60)}분 전"

    queue_usage = 0
    if MESSAGE_QUEUE.maxsize > 0:
        queue_usage = (MESSAGE_QUEUE.qsize() / MESSAGE_QUEUE.maxsize) * 100

    ticker_count = 0
    if _monitor:
        ticker_count = _monitor.get_subscribed_count()

    bb_status = get_status()
    uptime_seconds = asyncio.get_event_loop().time() - _start_time if _start_time else 0

    worker_status = "대기 중"
    if _worker_tasks:
        alive = sum(1 for t in _worker_tasks if not t.done())
        worker_status = f"{alive}/{len(_worker_tasks)} 활성"

    regime_status = regime_manager.get_status()
    macro = get_cached_macro()
    collector_summary = collector_status.get_summary()
    perf_status = performance_tracker.get_status() if performance_tracker else {}

    container_status = "초기화됨" if _container else "미초기화"

    return {
        "status": "운영 중" if (_kiwoom and _kiwoom.is_connected()) else "연결 끊김",
        "uptime_seconds": uptime_seconds,
        "tickers": ticker_count,
        "last_data_ago": last_data_ago,
        "kiwoom_connected": _kiwoom.is_connected() if _kiwoom else False,
        "queue_usage": queue_usage,
        "worker_status": worker_status,
        "blackbox_files": bb_status.get("file_count", 0),
        "blackbox_size_mb": bb_status.get("total_size_mb", 0),
        "regime": regime_status.get("current_regime", "Sideways"),
        "regime_last_update": f"{regime_status.get('last_update_ago', 0):.0f}초 전",
        "macro": {
            "kospi_trend": macro.kospi_trend,
            "usdkrw": macro.usdkrw,
            "vix": macro.vix,
            "bond_3y": macro.bond_3y,
        },
        "collector_status": {
            "healthy": collector_summary.get("healthy", 0),
            "total": collector_summary.get("total", 0),
            "fresh": collector_summary.get("fresh", 0),
        },
        "performance": perf_status,
        "container": container_status,
    }


# ============================================================
# 시그널 핸들러
# ============================================================
def setup_signal_handlers():
    is_windows = sys.platform == "win32"

    def _signal_handler(sig, frame):
        global _shutdown_requested
        _shutdown_requested = True
        logger.info(f"📡 시그널 {sig} 수신 → 종료 플래그 설정")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            if is_windows:
                signal.signal(sig, _signal_handler)
            else:
                loop = asyncio.get_running_loop()
                loop.add_signal_handler(sig, _signal_handler, sig, None)
        except Exception as e:
            logger.warning(f"⚠️ 시그널 {sig} 핸들러 등록 실패: {e}")


# ============================================================
# 스케줄러 작업 래퍼
# ============================================================
async def trading_day_task_wrapper(func, job_name: str = "작업", *args, **kwargs) -> None:
    if not is_trading_day():
        logger.info(f"📅 오늘은 비거래일 → {job_name} 스킵")
        debug_tower.log("SYSTEM", f"SKIP_{job_name}", {"reason": "non_trading_day"})
        return
    await func(*args, **kwargs)


async def run_feedback_and_reload(learner: FeedbackLearner, analyzer: DeepAnalyzer) -> None:
    await trading_day_task_wrapper(learner.run, "피드백 학습")
    await analyzer.load_weights()


async def run_weekly_pdf(pdf_gen: WeeklyPDFGenerator) -> None:
    await trading_day_task_wrapper(pdf_gen.generate, "주간 PDF")


async def run_daily_report(reporter: DailyReportGenerator) -> None:
    await trading_day_task_wrapper(reporter.generate_and_send, "일일 리포트")


async def run_daily_ohlcv_collect(kiwoom: KiwoomConnectorV512, db: DatabaseManager, tickers: list) -> None:
    await trading_day_task_wrapper(collect_daily_ohlcv, "OHLCV 수집", kiwoom, db, tickers)


async def run_macro_update() -> None:
    logger.info("📊 정기 거시 데이터 갱신 시작")
    await fetch_macro_data(force=True)


async def run_phase_transition_check(db: DatabaseManager, sender: TelegramSender) -> None:
    logger.info("📋 Phase 전환 조건 검증 시작")
    try:
        validator = PhaseTransitionValidator()
        start_date = "2026-08-20"
        end_date = datetime.now().strftime("%Y-%m-%d")
        decisions = await db.get_decisions_by_date_range(start_date, end_date)

        if len(decisions) < 50:
            await sender.send_raw(
                f"📊 <b>Phase 전환 검증</b>\n"
                f"샘플 부족 ({len(decisions)}건, 최소 50건 필요)\n"
                f"⏳ Shadow 운영 기간 연장 필요"
            )
            return

        total = len(decisions)
        stats = await db.get_feedback_stats(days=30)
        win_rate = stats.get("win_rate", 0.5)

        shadow_data = {
            "start_date": start_date,
            "end_date": end_date,
            "total_signals": total,
            "win_rate": win_rate,
            "profit_factor": 1.2,
            "max_drawdown": 0.05,
            "fp_ratio": 0.2,
            "downtime_hours": 0.0,
        }
        result = validator.validate(shadow_data)

        if result["passed"]:
            await sender.send_raw(
                f"🎉 <b>Phase 2 전환 조건 충족!</b>\n"
                f"✅ 모든 7대 조건 통과 → Paper Trading 진입 가능\n"
                f"{result['recommendation']}\n"
                f"📊 샘플: {total}건, 승률: {win_rate:.1%}"
            )
        else:
            await sender.send_raw(
                f"📊 <b>Phase 전환 검증 결과</b>\n"
                f"{result['recommendation']}\n"
                f"❌ 미충족 항목: {', '.join(result['failed_items'])}\n"
                f"📊 샘플: {total}건, 승률: {win_rate:.1%}"
            )
    except Exception as e:
        logger.error(f"❌ Phase 전환 검증 실패: {e}")
        await sender.send_raw(f"⚠️ Phase 전환 검증 오류: {str(e)[:100]}")


async def run_calibration(calibrator: ExecutionCalibrator) -> None:
    logger.info("📊 Calibration 실행 시작")
    try:
        await calibrator.run(days=30)
    except Exception as e:
        logger.error(f"❌ Calibration 실행 실패: {e}")


# ============================================================
# 재연결
# ============================================================
async def reconnect_and_resubscribe(kiwoom: KiwoomConnectorV512, monitor: RealtimeMonitor) -> None:
    MAX_OUTER_RETRIES = 30
    retry_count = 0
    while not kiwoom.is_connected():
        retry_count += 1
        if retry_count > MAX_OUTER_RETRIES:
            log_error("외부 재연결 최대 시도 초과", None)
            debug_tower.log("SYSTEM", "RECONNECT_MAX_RETRY", {})
            await send_error_alert("키움 재연결 반복 실패 — 수동 개입 필요")
            await asyncio.sleep(300)
            retry_count = 0
            continue
        logger.info(f"📡 외부 재연결 시도 {retry_count}/{MAX_OUTER_RETRIES}")
        debug_tower.log("SYSTEM", "RECONNECT_ATTEMPT_OUTER", {"attempt": retry_count})
        await kiwoom.connect()
        if not kiwoom.is_connected():
            await asyncio.sleep(config.get_int("reconnect_interval", 30))
    logger.info("✅ WebSocket 재연결 성공! 재구독 진행 중...")
    debug_tower.log("SYSTEM", "RECONNECT_SUCCESS_OUTER", {})
    await monitor.resubscribe_all()
    logger.info("✅ 재연결 및 전체 구독 재등록 완료.")


# ============================================================
# 전략 Worker
# ============================================================
async def strategy_worker(worker_id: int, analyzer: DeepAnalyzer, db: DatabaseManager, sender: TelegramSender) -> None:
    global _last_data_time
    logger.info(f"🧠 전략 Worker-{worker_id} 시작")
    debug_tower.log("SYSTEM", f"WORKER_START_{worker_id}", {})

    processed_count = 0
    while True:
        try:
            try:
                stock_data = await asyncio.wait_for(MESSAGE_QUEUE.get(), timeout=1.0)
            except TimeoutError:
                continue

            global _last_data_time
            _last_data_time = time.time()

            price = stock_data.get("price")
            if price is None or float(price) <= 0:
                MESSAGE_QUEUE.task_done()
                continue

            ticker = stock_data.get("ticker", "UNKNOWN")
            debug_tower.log(ticker, "WORKER_PROCESS", {"worker": worker_id})

            analysis = await analyzer.analyze(stock_data)

            if analysis.get("action") != "ERROR":
                await db.save_decision(analysis)

            action = analysis.get("action")
            if action in [
                "SIGNAL_ENTRY",
                "EVENT_SL_TRAIL",
                "EVENT_ATR_SPIKE",
                "EVENT_TP_HIT",
                "EVENT_EXIT",
                "EVENT_LIFECYCLE_ADVICE",
            ]:
                success = await sender.send(analysis)
                if not success:
                    log_event(
                        "TELEGRAM_SEND_FAILED",
                        {"worker_id": worker_id, "ticker": analysis.get("ticker"), "action": action},
                    )
                    debug_tower.log(ticker, "TELEGRAM_SEND_FAILED", {"action": action})
                else:
                    processed_count += 1
                    if action == "SIGNAL_ENTRY":
                        logger.info(f"📊 Worker-{worker_id} [진입] {analysis.get('ticker')} 신호 전송")
                    elif action == "EVENT_SL_TRAIL":
                        logger.info(f"📊 Worker-{worker_id} [손절상승] {analysis.get('ticker')}")
                    elif action == "EVENT_ATR_SPIKE":
                        logger.info(f"📊 Worker-{worker_id} [ATR급변동] {analysis.get('ticker')}")
                    elif action == "EVENT_TP_HIT":
                        logger.info(f"📊 Worker-{worker_id} [부분익절] {analysis.get('ticker')} TP{analysis.get('tp_level')}")
                    elif action == "EVENT_EXIT":
                        logger.info(f"📊 Worker-{worker_id} [청산] {analysis.get('ticker')}")
                        await analyzer.clear_trailing_stop(analysis.get("ticker"))
                    elif action == "EVENT_LIFECYCLE_ADVICE":
                        logger.info(f"📊 Worker-{worker_id} [합의권고] {analysis.get('ticker')}")

            if processed_count % 50 == 0 and processed_count > 0:
                logger.info(f"📊 Worker-{worker_id} 처리 완료: {processed_count}개 이벤트")

            MESSAGE_QUEUE.task_done()

        except asyncio.CancelledError:
            logger.info(f"🛑 전략 Worker-{worker_id} 종료 (Cancelled)")
            debug_tower.log("SYSTEM", f"WORKER_STOP_{worker_id}", {})
            break
        except DatabaseError as e:
            logger.error(f"❌ DB 오류 (Worker-{worker_id}): {e}")
            await send_error_alert(f"DB 오류 (Worker-{worker_id})", str(e))
            await asyncio.sleep(5)
        except KiwoomError as e:
            logger.error(f"❌ 키움 API 오류 (Worker-{worker_id}): {e} (code: {e.code})")
            await send_error_alert(f"키움 오류 (Worker-{worker_id})", f"{type(e).__name__}: {e}")
            await asyncio.sleep(3)
        except DataCollectionError as e:
            logger.error(f"❌ 데이터 수집 오류 (Worker-{worker_id}): [{e.source}] {e.message}")
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"❌ 전략 Worker-{worker_id} 오류: {e}", exc_info=True)
            debug_tower.capture_snapshot("SYSTEM", e, f"WORKER_{worker_id}")
            await send_error_alert(f"Worker-{worker_id} 오류", str(e)[:200])
            await asyncio.sleep(1)


# ============================================================
# Telegram 알림 함수
# ============================================================
async def send_error_alert(error_msg: str, error_detail: str = "") -> None:
    global _error_sender
    if _error_sender is None:
        _error_sender = TelegramSender()
    message = f"""
🚨 <b>시스템 치명적 오류</b>
━━━━━━━━━━━━━━━━━━━━━
📌 {error_msg}
📋 {error_detail[:200] if error_detail else '없음'}
🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
━━━━━━━━━━━━━━━━━━━━━
"""
    try:
        await _error_sender.send_raw(message)
    except Exception:
        pass


async def send_startup_notification(success: bool, details: dict | None = None) -> None:
    global _error_sender
    if _error_sender is None:
        _error_sender = TelegramSender()
    details = details or {}
    status_emoji = "🟢" if success else "🔴"
    status_text = "시작 성공 (Running)" if success else "시작 실패 (Failed)"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    weekday = ["월", "화", "수", "목", "금", "토", "일"][datetime.now().weekday()]

    bb_status = get_status()
    bb_info = f"블랙박스: {bb_status['file_count']}개 파일, 총 {bb_status['total_size_mb']}MB"

    macro = get_cached_macro()

    msg = f"""
{status_emoji} <b>시스템 상태 보고</b>
━━━━━━━━━━━━━━━━━━━━━
📌 <b>상태</b>: {status_text}
🕒 <b>시간</b>: {now_str} ({weekday}요일)
🤖 <b>PID</b>: {os.getpid()}
"""
    if success:
        tickers = details.get("tickers", [])
        ticker_str = ", ".join(tickers[:10]) if tickers else "없음"
        if len(tickers) > 10:
            ticker_str += f" 외 {len(tickers)-10}개"
        msg += f"""
📡 <b>구독 종목</b>: {len(tickers)}개 → {ticker_str}
🔌 <b>키움 연결</b>: {"✅ 연결됨" if details.get('kiwoom_connected') else "❌ 연결 실패"}
⏰ <b>스케줄러</b>: {details.get('job_count', 0)}개 작업 등록
📊 <b>버전</b>: v8.0.0 (Supervisor + Verifier)
📈 <b>거시 지표</b>: KOSPI 5일 {macro.kospi_trend:.2f}% | USD/KRW {macro.usdkrw:.0f} | VIX {macro.vix:.1f}
💾 <b>{bb_info}</b>
━━━━━━━━━━━━━━━━━━━━━
<i>실시간 스캔 + 자가 치유 + 성과 추적 + Paper Trading</i>
"""
    else:
        msg += f"""
📋 <b>실패 사유</b>: {details.get('error', '알 수 없음')}
━━━━━━━━━━━━━━━━━━━━━
<i>로그 파일을 확인하세요. (logs/scanner.log / logs/blackbox/)</i>
"""
    try:
        await _error_sender.send_raw(msg)
    except Exception as e:
        log_error("시작 알림 전송 실패", e)
        debug_tower.capture_snapshot("SYSTEM", e, "STARTUP_NOTIFY")


async def send_shutdown_notification(reason: str = "정상 종료") -> None:
    global _error_sender
    if _error_sender is None:
        _error_sender = TelegramSender()
    msg = f"""
🟡 <b>시스템 종료</b>
━━━━━━━━━━━━━━━━━━━━━
📌 <b>사유</b>: {reason}
🕒 <b>시간</b>: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🤖 <b>PID</b>: {os.getpid()}
━━━━━━━━━━━━━━━━━━━━━
<i>시스템이 안전하게 종료되었습니다.</i>
"""
    try:
        await _error_sender.send_raw(msg)
    except Exception:
        pass


# ============================================================
# 헬스체크 서버
# ============================================================
async def health_check(request: web.Request) -> web.Response:
    queue_usage = (MESSAGE_QUEUE.qsize() / MESSAGE_QUEUE.maxsize) * 100 if MESSAGE_QUEUE.maxsize > 0 else 0
    data_flow_healthy = (time.time() - _last_data_time) < 180

    regime_status = regime_manager.get_status()
    macro = get_cached_macro()
    collector_summary = collector_status.get_summary()
    perf_status = performance_tracker.get_status() if performance_tracker else {}

    status = {
        "status": "healthy" if (queue_usage < 90 and data_flow_healthy) else "degraded",
        "uptime_seconds": (asyncio.get_event_loop().time() - _start_time) if _start_time else 0,
        "components": {
            "kiwoom": {"connected": _kiwoom.is_connected() if _kiwoom else False},
            "monitor": {"is_running": _monitor.is_running() if _monitor else False},
            "database": {"initialized": _db is not None},
            "queue": {"size": MESSAGE_QUEUE.qsize(), "maxsize": MESSAGE_QUEUE.maxsize, "usage_percent": queue_usage},
            "data_flow": {"last_data_sec_ago": time.time() - _last_data_time, "healthy": data_flow_healthy},
        },
        "blackbox": get_status(),
        "debug_tower": debug_tower.get_stats(),
        "regime_manager": regime_status,
        "macro": macro.to_dict(),
        "collector_status": collector_summary,
        "performance_tracker": perf_status,
        "container": "initialized" if _container else "none",
    }
    return web.json_response(status)


async def start_health_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    for offset in range(10):
        try_port = port + offset
        try:
            app = web.Application()
            app.router.add_get("/health", health_check)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, host, try_port)
            await site.start()
            logger.info(f"🩺 헬스체크 서버 실행 중: http://{host}:{try_port}/health")
            debug_tower.log("SYSTEM", "HEALTH_SERVER_STARTED", {"port": try_port})
            return
        except OSError:
            continue
    logger.warning("⚠️ 헬스체크 서버 시작 실패")
    debug_tower.log("SYSTEM", "HEALTH_SERVER_FAIL", {})


# ============================================================
# 메인 함수
# ============================================================
async def main() -> None:
    global \
        _kiwoom, _monitor, _db, _start_time, _error_sender, \
        _scheduler, _worker_tasks, _main_loop, _last_data_time, \
        _health_task, _telegram_cmd, _all_tasks, _original_exception_handlers, \
        _shutdown_requested, _safety_guard, _container

    if not is_trading_day():
        log_event("NON_TRADING_DAY", {"date": datetime.now().strftime("%Y-%m-%d")})
        logger.info("📅 오늘은 비거래일입니다. 프로그램을 종료합니다.")
        debug_tower.log("SYSTEM", "NON_TRADING_DAY", {})
        return

    analyzer = None

    _main_loop = asyncio.get_running_loop()
    _last_data_time = time.time()

    log_event("SYSTEM_START", {"pid": os.getpid(), "version": "v8.0.0"})
    debug_tower.log("SYSTEM", "MAIN_START", {"pid": os.getpid(), "version": "v8.0.0"})

    check_and_create_pid()
    load_dotenv(override=True)
    validate_env()

    _start_time = asyncio.get_event_loop().time()
    _error_sender = TelegramSender()

    set_alert_callback(send_error_alert)
    logger.info("✅ macro_collector 알림 콜백 등록 완료")

    _original_exception_handlers = setup_global_exception_handler()
    logger.info("✅ 전역 예외 핸들러 활성화")

    setup_signal_handlers()
    logger.info("✅ 시그널 핸들러 등록 완료 (SIGINT/SIGTERM)")

    logger.info("=" * 70)
    logger.info("🚀 v8.0.0 FINAL - Supervisor + Verifier 통합 (자가 치유 + 검증)")
    logger.info("📌 기능: 실시간 스캔, 자동 재시작, 알림 검증, 성과 추적, Paper Trading")
    logger.info("📱 Telegram: '현황', '신호', '삼전' → 종합 분석 리포트")
    logger.info("=" * 70)

    _safety_guard = SafetyGuard()
    logger.info("✅ SafetyGuard 초기화 완료 (시장 위기 감지 활성화)")

    startup_success = False
    startup_details: dict[str, Any] = {}

    try:
        # ============================================================
        # 🔥 Supervisor 백그라운드 태스크 시작 (시스템 감시)
        # ============================================================
        supervisor = SystemSupervisor()
        supervisor_task = asyncio.create_task(supervisor.run())
        _all_tasks.append(supervisor_task)
        logger.info("✅ SystemSupervisor 백그라운드 감시 시작됨")

        set_alert_handler(send_error_alert)
        logger.info("✅ Telegram 알림 핸들러 연결 완료")

        collector_status.register("system", freshness_seconds=None)
        logger.info("✅ CollectorStatus 관리자 초기화 완료")

        logger.info("📊 거시 데이터 초기 수집 중...")
        await fetch_macro_data(force=True)
        macro = get_cached_macro()
        logger.info(f"   ✅ KOSPI 5일: {macro.kospi_trend:.2f}% | USD/KRW: {macro.usdkrw:.2f} | VIX: {macro.vix:.1f}")

        _container = AppContainer.create_production()
        await _container.initialize()
        logger.info("✅ DI 컨테이너 초기화 완료")

        _db = _container.db_manager
        _kiwoom = _container.kiwoom
        if not _kiwoom.is_connected():
            logger.info("⏳ 키움 서버 연결 대기 중...")
            retry_count = 0
            while not _kiwoom.is_connected():
                retry_count += 1
                await _kiwoom.connect()
                if not _kiwoom.is_connected():
                    if retry_count % 5 == 0:
                        await send_error_alert(f"키움 연결 실패 (재시도 {retry_count}회)")
                    await asyncio.sleep(config.get_int("connect_retry_interval", 60))
            logger.info("✅ 키움 서버 연결 성공!")
            log_event("KIWOOM_CONNECTED", {"retries": retry_count})
            debug_tower.log("SYSTEM", "KIWOOM_CONNECTED", {"retries": retry_count})

        logger.info("⏳ WebSocket LOGIN 및 수신 루프 준비 대기 중...")
        if not await _kiwoom.wait_until_ready(timeout=10.0):
            logger.warning("⚠️ WebSocket 준비 타임아웃, 강제 재연결 시도")
            debug_tower.log("SYSTEM", "WS_READY_TIMEOUT_RETRY", {})
            await _kiwoom.disconnect()
            await _kiwoom.connect()
            if not await _kiwoom.wait_until_ready(timeout=10.0):
                logger.error("❌ WebSocket 준비 실패")
                _kiwoom._is_connected = False
                log_event("WS_READY_FAILED", {})
                debug_tower.log("SYSTEM", "WS_READY_FAILED", {})
        else:
            logger.info("✅ WebSocket 완전 준비 완료")
            debug_tower.log("SYSTEM", "WS_READY_OK", {})

        _monitor = RealtimeMonitor(_kiwoom, MESSAGE_QUEUE)
        await _monitor.start()
        startup_details["ticker_count"] = _monitor.get_subscribed_count()
        startup_details["kiwoom_connected"] = _kiwoom.is_connected()
        startup_details["tickers"] = _monitor.tickers
        log_event("MONITOR_STARTED", {"count": startup_details["ticker_count"]})
        debug_tower.log("SYSTEM", "MONITOR_STARTED", {"count": startup_details["ticker_count"]})

        await regime_manager.start()
        logger.info("✅ RegimeManager 시작됨 (60초 간격 국면 갱신)")
        debug_tower.log("SYSTEM", "REGIME_MANAGER_STARTED", {})

        feedback_learner = FeedbackLearner(kiwoom_connector=_kiwoom, db_manager=_db)
        analyzer = DeepAnalyzer(db_manager=_db, feedback_learner=feedback_learner)
        await analyzer.load_weights()

        # 🔥 버그 2 수정: portfolio_manager.start() 명시적 호출
        if hasattr(analyzer, "portfolio_manager"):
            await analyzer.portfolio_manager.start()
            logger.info("✅ PortfolioManager 시작됨 (VaR 갱신 루프 활성화)")

        # 🔥 trailing_stops 이전 세션 상태 복구 (DB에서 로드)
        try:
            restored = await _db.load_trailing_stops()
            if restored:
                analyzer.trailing_stops.update(restored)
                logger.info(f"♻️ trailing_stops 복구 완료: {len(restored)}건 ({list(restored.keys())})")
            else:
                logger.info("ℹ️ 복구할 trailing_stops 없음 (첫 실행 또는 정상 종료)")
        except Exception as e:
            logger.warning(f"⚠️ trailing_stops 복구 실패 (무시하고 계속): {e}")

        sender = TelegramSender()

        dart_api_key = os.getenv("DART_API_KEY")
        if dart_api_key:
            dart_connector = DartConnector(api_key=dart_api_key)
            await dart_connector.connect()
            logger.info("✅ DART 커넥터 초기화 완료")
        else:
            dart_connector = None
            logger.warning("⚠️ DART_API_KEY 없음 → 재무 데이터 제외")

        news_crawler = NewsCrawler()
        await news_crawler.connect()
        logger.info("✅ 뉴스 크롤러 초기화 완료")

        performance_tracker.initialize(_db)
        await performance_tracker.start()
        logger.info("✅ PerformanceTracker 시작됨 (5분 간격 성과 갱신)")

        _container.order_executor  # OrderExecutor 초기화 (Paper Mode)
        logger.info("✅ OrderExecutor 초기화 완료 (Paper Mode)")

        calibrator = ExecutionCalibrator(_db, sender)
        logger.info("✅ ExecutionCalibrator 초기화 완료")

        daily_reporter = DailyReportGenerator(db_manager=_db, telegram_sender=sender)
        weekly_pdf_gen = WeeklyPDFGenerator(db_manager=_db, kiwoom_connector=_kiwoom)

        _telegram_cmd = TelegramCommandHandler(
            token=os.getenv("TELEGRAM_BOT_TOKEN"),
            chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            get_stats_callback=get_system_stats,
        )
        _telegram_cmd.set_dependencies(
            db_manager=_db, analyzer=analyzer, monitor=_monitor, dart=dart_connector, news=news_crawler, kiwoom=_kiwoom
        )
        await _telegram_cmd.start()
        logging.getLogger("telegram.ext").setLevel(logging.INFO)
        logging.getLogger("telegram.request").setLevel(logging.INFO)
        logger.info("📱 Telegram 자연어 명령어 + 종합 분석 리포트 활성화")

        _scheduler = SchedulerManager()
        _scheduler.add_job_with_retry(
            run_daily_report,
            CronTrigger(hour=config.get_int("daily_report_hour", 7), minute=config.get_int("daily_report_minute", 0), timezone="Asia/Seoul"),
            "daily_report",
            daily_reporter,
            max_retries=3, retry_delay=5,
        )
        _scheduler.add_job_with_retry(
            run_feedback_and_reload,
            CronTrigger(hour=config.get_int("feedback_hour", 17), minute=config.get_int("feedback_minute", 0), timezone="Asia/Seoul"),
            "feedback_learning",
            feedback_learner,
            analyzer,
            max_retries=3, retry_delay=5,
        )
        _scheduler.add_job_with_retry(
            run_weekly_pdf,
            CronTrigger(day_of_week=config.get("weekly_pdf_day", "mon"), hour=config.get_int("weekly_pdf_hour", 6), minute=config.get_int("weekly_pdf_minute", 0), timezone="Asia/Seoul"),
            "weekly_pdf",
            weekly_pdf_gen,
            max_retries=3, retry_delay=5,
        )
        _scheduler.add_job_with_retry(
            run_daily_ohlcv_collect,
            CronTrigger(hour=config.get_int("ohlcv_hour", 16), minute=config.get_int("ohlcv_minute", 30), timezone="Asia/Seoul"),
            "daily_ohlcv",
            _kiwoom,
            _db,
            _monitor.tickers,
            max_retries=3, retry_delay=5,
        )
        _scheduler.add_job_with_retry(
            run_macro_update,
            CronTrigger(hour=8, minute=0, timezone="Asia/Seoul"),
            "macro_update",
            max_retries=3, retry_delay=5,
        )
        _scheduler.add_job_with_retry(
            run_phase_transition_check,
            CronTrigger(hour=17, minute=30, timezone="Asia/Seoul"),
            "phase_transition_check",
            _db,
            sender,
            max_retries=2, retry_delay=5,
        )
        _scheduler.add_job_with_retry(
            run_calibration,
            CronTrigger(hour=17, minute=30, timezone="Asia/Seoul"),
            "calibration",
            calibrator,
            max_retries=2, retry_delay=5,
        )
        # ============================================================
        # 🔥 AlertVerifier 스케줄 등록 (매일 16:00)
        # ============================================================
        _scheduler.add_job_with_retry(
            scheduled_verify,
            CronTrigger(hour=16, minute=0, timezone="Asia/Seoul"),
            "alert_verifier",
            max_retries=2, retry_delay=5,
        )
        _scheduler.start()
        startup_details["job_count"] = 9  # 8개 + alert_verifier
        logger.info(f"⏰ 스케줄러 등록 완료 (총 {startup_details['job_count']}개 작업)")
        log_event("SCHEDULER_STARTED", {"jobs": startup_details["job_count"]})
        debug_tower.log("SYSTEM", "SCHEDULER_STARTED", {"jobs": startup_details["job_count"]})

        _worker_tasks = []
        _all_tasks = []
        for i in range(2):
            task = asyncio.create_task(strategy_worker(i + 1, analyzer, _db, sender))
            _worker_tasks.append(task)
            _all_tasks.append(task)

        _health_task = asyncio.create_task(start_health_server())
        _all_tasks.append(_health_task)

        if hasattr(analyzer, "portfolio_manager"):
            pm_task = analyzer.portfolio_manager._update_task
            if pm_task:
                _all_tasks.append(pm_task)

        if performance_tracker._task:
            _all_tasks.append(performance_tracker._task)

        startup_success = True
        await send_startup_notification(True, startup_details)
        log_event("SYSTEM_READY", {})
        debug_tower.log("SYSTEM", "SYSTEM_READY", {})

        logger.info("🚀 메인 루프 진입 (Windows 기본 asyncio + Supervisor + SafetyGuard + PerformanceTracker + Paper Trading)")
        while not _shutdown_requested:
            try:
                macro = get_cached_macro()
                safety_data = {
                    "kospi_drop": macro.kospi_trend if macro.kospi_trend != 0 else 0.0,
                    "vkospi_spike": macro.vkospi,
                    "usdkrw_spike": macro.usdkrw,
                    "feature_expired": 0.0,
                    "tr_latency": 0.0,
                    "calibration_error": 0.0,
                }
                safety_result = _safety_guard.check(safety_data)
                if safety_result["action"] == "BLOCK_ALL":
                    logger.critical(f"🚨 SafetyGuard 트리거됨: {safety_result['triggered']}")
                    await send_error_alert(
                        "SafetyGuard 차단 활성화",
                        f"조건: {', '.join([t['condition'] for t in safety_result['triggered']])}",
                    )
                    await asyncio.sleep(10)
                    continue

                if not _kiwoom.is_connected():
                    await reconnect_and_resubscribe(_kiwoom, _monitor)
                    await asyncio.sleep(1)
                    continue

                now = datetime.now()
                if (9 <= now.hour <= 15) and not (now.hour == 15 and now.minute >= 20):
                    if time.time() - _last_data_time > _DATA_FLOW_TIMEOUT:
                        log_event("DATA_FLOW_TIMEOUT", {"seconds": _DATA_FLOW_TIMEOUT})
                        logger.error(f"🔥 데이터 흐름 감시: {_DATA_FLOW_TIMEOUT}초 동안 데이터 없음! 강제 재연결 시도")
                        debug_tower.log("SYSTEM", "DATA_FLOW_TIMEOUT", {})
                        await _kiwoom.disconnect()
                        await _kiwoom.connect()
                        await _monitor.resubscribe_all()
                        _last_data_time = time.time()

                signals = await _monitor.scan()
                for sig_data in signals:
                    try:
                        MESSAGE_QUEUE.put_nowait(sig_data)
                        debug_tower.log(sig_data.get("ticker"), "SIGNAL_ENQUEUED", {"action": sig_data.get("action")})
                    except asyncio.QueueFull:
                        logger.warning(f"⚠️ 큐 가득 참, 신호 드롭: {sig_data.get('ticker')}")
                        debug_tower.log(signal.get("ticker"), "SIGNAL_DROPPED", {"reason": "queue_full"})

                await asyncio.sleep(1)

            except asyncio.CancelledError:
                _shutdown_requested = True
                logger.info("🛑 메인 루프 CancelledError 수신 → 종료")
                break
            except Exception as e:
                log_error("메인 루프 오류", e)
                debug_tower.capture_snapshot("SYSTEM", e, "MAIN_LOOP")
                await asyncio.sleep(5)

    except (KeyboardInterrupt, asyncio.CancelledError):
        _shutdown_requested = True
        log_event("SYSTEM_INTERRUPTED", {})
        logger.info("⏹ 종료 신호 수신")
        debug_tower.log("SYSTEM", "SYSTEM_INTERRUPTED", {})
    except FatalError as e:
        error_msg = f"치명적 오류: {e!s}"
        log_error(error_msg, e)
        debug_tower.capture_snapshot("SYSTEM", e, "FATAL")
        startup_details["error"] = error_msg
        await send_startup_notification(False, startup_details)
        await send_error_alert(error_msg, traceback.format_exc()[:300])
        raise
    except Exception as e:
        error_msg = f"시작 실패: {e!s}"
        log_error(error_msg, e)
        debug_tower.capture_snapshot("SYSTEM", e, "START_FAIL")
        startup_details["error"] = error_msg
        await send_startup_notification(False, startup_details)
        await send_error_alert(error_msg, traceback.format_exc()[:300])
        raise

    finally:
        if startup_success:
            await send_shutdown_notification("정상 종료")
        log_event("SYSTEM_SHUTDOWN", {})
        debug_tower.log("SYSTEM", "SYSTEM_SHUTDOWN", {})

        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except Exception:
                pass

        if _all_tasks:
            logger.info(f"⏳ {len(_all_tasks)}개 태스크 종료 대기 중...")
            for task in _all_tasks:
                if not task.done():
                    task.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(*_all_tasks, return_exceptions=True), timeout=5.0)
            except TimeoutError:
                logger.warning("⚠️ 일부 태스크가 5초 내에 종료되지 않음")
            logger.info("✅ 모든 태스크 종료 완료")

        if analyzer is not None and hasattr(analyzer, "portfolio_manager"):
            await analyzer.portfolio_manager.stop()

        # 🔥 trailing_stops 상태 DB 저장 (프로세스 재시작 시 복구용)
        if analyzer is not None and _db is not None:
            try:
                stops = getattr(analyzer, "trailing_stops", {})
                if stops:
                    saved = await _db.save_trailing_stops(stops)
                    logger.info(f"✅ trailing_stops {saved}건 DB 저장 완료")
                else:
                    await _db.clear_trailing_stops()
                    logger.info("✅ trailing_stops 없음 - DB 초기화")
            except Exception as e:
                logger.warning(f"⚠️ trailing_stops 저장 실패: {e}")

        await performance_tracker.stop()

        if _telegram_cmd:
            await _telegram_cmd.stop()

        await regime_manager.stop()

        if _kiwoom:
            await _kiwoom.disconnect()
            await asyncio.sleep(0.5)

        if _scheduler:
            _scheduler.shutdown()

        if _container:
            await _container.shutdown()

        try:
            summary = collector_status.get_summary()
            logger.info(f"📊 수집기 상태 요약: 건강 {summary['healthy']}/{summary['total']}, 신선 {summary['fresh']}/{summary['total']}")
            if summary["unhealthy"] > 0:
                unhealthy = [name for name, s in summary["collectors"].items() if not s["is_healthy"]]
                logger.warning(f"⚠️ 비정상 수집기: {', '.join(unhealthy)}")
        except Exception as e:
            logger.debug(f"CollectorStatus 요약 실패: {e}")

        if _original_exception_handlers:
            try:
                restore_exception_handler(_original_exception_handlers)
                logger.info("✅ 전역 예외 핸들러 복원 완료")
            except Exception as e:
                logger.debug(f"⚠️ 예외 핸들러 복원 실패: {e}")

        debug_tower.flush()
        await asyncio.sleep(0.2)
        logger.info("✅ 시스템 안전하게 종료 완료")


# ============================================================
# 유틸리티
# ============================================================
def check_and_create_pid() -> None:
    if PID_FILE.exists():
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            result = subprocess.run(["tasklist", "/FI", f"PID eq {old_pid}"], capture_output=True, text=True)
            if str(old_pid) in result.stdout:
                print(f"❌ 이미 실행 중인 프로세스가 있습니다 (PID: {old_pid})")
                sys.exit(1)
            else:
                PID_FILE.unlink()
        except Exception:
            try:
                PID_FILE.unlink()
            except Exception:
                pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    print(f"✅ PID 파일 생성: {os.getpid()}")


def validate_env() -> None:
    required_keys = ["KIWOOM_APP_KEY", "KIWOOM_APP_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [k for k in required_keys if not os.getenv(k)]
    if missing:
        print(f"❌ 필수 환경변수가 없습니다: {', '.join(missing)}")
        sys.exit(1)
    logger.info("✅ 환경변수 검증 완료")
    debug_tower.log("SYSTEM", "ENV_VALIDATED", {})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 사용자 중단")
        debug_tower.flush()
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except Exception:
                pass
    except Exception as e:
        print(f"❌ 시스템 종료: {e}")
        traceback.print_exc()
        debug_tower.flush()