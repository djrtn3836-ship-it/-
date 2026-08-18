#!/usr/bin/env python3
"""
scanner_main.py - v7.2.6 FINAL (실시간 모멘텀 스캔 루프 추가)
- SchedulerManager 시작/종료 시점 명확화
- wait_until_ready() 실패 시 is_connected를 False로 설정하여 재연결 유도
- finally에서 모든 태스크 완전 종료 후 스케줄러 종료
- 예외 발생 시 상세 로그 출력
- 🔥 strategy_worker: price<=0 틱 조기 차단, ERROR 액션 DB 저장 방지
- 🔥 메인 루프에서 1초마다 _monitor.scan() 호출하여 실시간 모멘텀 신호 생성
- 🔥 디버그 관제탑 적용
"""

import asyncio
import sys
import os
import subprocess
import traceback
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

sys.path.insert(0, str(Path(__file__).parent))

from apscheduler.triggers.cron import CronTrigger
from aiohttp import web

from core.logger import setup_logger
from core.scheduler import SchedulerManager
from core.holiday_utils import is_trading_day
from core.config import get_config
from core.exceptions import KiwoomError
from core.blackbox_logger import log_event, log_error, get_status
from core.debug_tower import debug_tower
from dotenv import load_dotenv

FatalError = Exception

from scanner.realtime_monitor import RealtimeMonitor
from scanner.deep_analyzer import DeepAnalyzer
from data.kiwoom_connector import KiwoomConnectorV512
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender
from report.daily_report import DailyReportGenerator
from report.weekly_pdf import WeeklyPDFGenerator
from feedback.feedback_learner import FeedbackLearner
from scheduler.daily_collector import collect_daily_ohlcv

logger = setup_logger("scanner")
config = get_config()

# --- 글로벌 변수 ---
_kiwoom: Optional[KiwoomConnectorV512] = None
_monitor: Optional[RealtimeMonitor] = None
_db: Optional[DatabaseManager] = None
_start_time: float = 0.0
_error_sender: Optional[TelegramSender] = None
_scheduler: Optional[SchedulerManager] = None
_worker_tasks: list = []
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_health_task: Optional[asyncio.Task] = None
PID_FILE = Path(__file__).parent / "scanner.pid"
MESSAGE_QUEUE: asyncio.Queue = asyncio.Queue(maxsize=config.get_int("queue_maxsize", 100000))

_last_data_time = 0.0
_DATA_FLOW_TIMEOUT = 180

# ============================================================
# 1. 유틸리티 함수
# ============================================================
def check_and_create_pid() -> None:
    if PID_FILE.exists():
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            result = subprocess.run(
                ['tasklist', '/FI', f'PID eq {old_pid}'],
                capture_output=True,
                text=True
            )
            if str(old_pid) in result.stdout:
                print(f"❌ 이미 실행 중인 프로세스가 있습니다 (PID: {old_pid})")
                sys.exit(1)
            else:
                PID_FILE.unlink()
        except:
            try:
                PID_FILE.unlink()
            except:
                pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    print(f"✅ PID 파일 생성: {os.getpid()}")

def validate_env() -> None:
    required_keys = ['KIWOOM_APP_KEY', 'KIWOOM_APP_SECRET', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID']
    missing = [k for k in required_keys if not os.getenv(k)]
    if missing:
        print(f"❌ 필수 환경변수가 없습니다: {', '.join(missing)}")
        sys.exit(1)
    logger.info("✅ 환경변수 검증 완료")
    debug_tower.log("SYSTEM", "ENV_VALIDATED", {})

# ============================================================
# 2. APScheduler 작업들
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

# ============================================================
# 3. 재연결 (무제한 루프 제한)
# ============================================================
async def reconnect_and_resubscribe(kiwoom: KiwoomConnectorV512, monitor: RealtimeMonitor) -> None:
    MAX_OUTER_RETRIES = 10
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
# 4. 전략 Worker
# ============================================================
async def strategy_worker(worker_id: int, analyzer: DeepAnalyzer, db: DatabaseManager, sender: TelegramSender) -> None:
    global _last_data_time
    logger.info(f"🧠 전략 Worker-{worker_id} 시작 (v7.2.6)")
    debug_tower.log("SYSTEM", f"WORKER_START_{worker_id}", {})

    processed_count = 0
    while True:
        try:
            try:
                stock_data = await asyncio.wait_for(MESSAGE_QUEUE.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            _last_data_time = time.time()

            price = stock_data.get('price')
            if price is None or float(price) <= 0:
                MESSAGE_QUEUE.task_done()
                continue

            ticker = stock_data.get('ticker', 'UNKNOWN')
            debug_tower.log(ticker, "WORKER_PROCESS", {"worker": worker_id})

            analysis = await analyzer.analyze(stock_data)

            if analysis.get('action') != 'ERROR':
                await db.save_decision(analysis)

            action = analysis.get('action')
            if action in [
                "SIGNAL_ENTRY", "EVENT_SL_TRAIL", "EVENT_ATR_SPIKE",
                "EVENT_TP_HIT", "EVENT_EXIT", "EVENT_LIFECYCLE_ADVICE"
            ]:
                success = await sender.send(analysis)
                if not success:
                    log_event("TELEGRAM_SEND_FAILED", {
                        "worker_id": worker_id,
                        "ticker": analysis.get('ticker'),
                        "action": action
                    })
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
                        await analyzer.clear_trailing_stop(analysis.get('ticker'))
                    elif action == "EVENT_LIFECYCLE_ADVICE":
                        logger.info(f"📊 Worker-{worker_id} [합의권고] {analysis.get('ticker')}")

            if processed_count % 50 == 0 and processed_count > 0:
                logger.info(f"📊 Worker-{worker_id} 처리 완료: {processed_count}개 이벤트")

            MESSAGE_QUEUE.task_done()

        except asyncio.CancelledError:
            logger.info(f"🛑 전략 Worker-{worker_id} 종료")
            debug_tower.log("SYSTEM", f"WORKER_STOP_{worker_id}", {})
            break
        except Exception as e:
            logger.error(f"❌ 전략 Worker-{worker_id} 오류: {e}", exc_info=True)
            debug_tower.capture_snapshot("SYSTEM", e, f"WORKER_{worker_id}")
            await asyncio.sleep(1)

# ============================================================
# 5. Telegram 알림 함수
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
    except:
        pass

async def send_startup_notification(success: bool, details: Optional[Dict] = None) -> None:
    global _error_sender
    if _error_sender is None:
        _error_sender = TelegramSender()
    details = details or {}
    status_emoji = "🟢" if success else "🔴"
    status_text = "시작 성공 (Running)" if success else "시작 실패 (Failed)"
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    weekday = ["월", "화", "수", "목", "금", "토", "일"][datetime.now().weekday()]

    bb_status = get_status()
    bb_info = f"블랙박스: {bb_status['file_count']}개 파일, 총 {bb_status['total_size_mb']}MB"

    msg = f"""
{status_emoji} <b>시스템 상태 보고</b>
━━━━━━━━━━━━━━━━━━━━━
📌 <b>상태</b>: {status_text}
🕒 <b>시간</b>: {now_str} ({weekday}요일)
🤖 <b>PID</b>: {os.getpid()}
"""
    if success:
        tickers = details.get('tickers', [])
        ticker_str = ', '.join(tickers[:10]) if tickers else '없음'
        if len(tickers) > 10:
            ticker_str += f' 외 {len(tickers)-10}개'
        msg += f"""
📡 <b>구독 종목</b>: {len(tickers)}개 → {ticker_str}
🔌 <b>키움 연결</b>: {"✅ 연결됨" if details.get('kiwoom_connected') else "❌ 연결 실패"}
⏰ <b>스케줄러</b>: {details.get('job_count', 0)}개 작업 등록
📊 <b>버전</b>: v7.2.6 FINAL (실시간 모멘텀 스캔)
💾 <b>{bb_info}</b>
━━━━━━━━━━━━━━━━━━━━━
<i>실시간 스캔 + 이벤트 기반 알림 + 자가 치유</i>
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
    except:
        pass

# ============================================================
# 6. 헬스체크 서버
# ============================================================
async def health_check(request: web.Request) -> web.Response:
    global _kiwoom, _monitor, _db, _start_time, _last_data_time
    queue_usage = (MESSAGE_QUEUE.qsize() / MESSAGE_QUEUE.maxsize) * 100 if MESSAGE_QUEUE.maxsize > 0 else 0
    data_flow_healthy = (time.time() - _last_data_time) < 180
    status = {
        "status": "healthy" if (queue_usage < 90 and data_flow_healthy) else "degraded",
        "uptime_seconds": (asyncio.get_event_loop().time() - _start_time) if _start_time else 0,
        "components": {
            "kiwoom": {"connected": _kiwoom.is_connected() if _kiwoom else False},
            "monitor": {"is_running": _monitor.is_running() if _monitor else False},
            "database": {"initialized": _db is not None},
            "queue": {"size": MESSAGE_QUEUE.qsize(), "maxsize": MESSAGE_QUEUE.maxsize, "usage_percent": queue_usage},
            "data_flow": {"last_data_sec_ago": time.time() - _last_data_time, "healthy": data_flow_healthy}
        },
        "blackbox": get_status(),
        "debug_tower": debug_tower.get_stats()
    }
    return web.json_response(status)

async def start_health_server(host: str = '0.0.0.0', port: int = 8080) -> None:
    for offset in range(10):
        try_port = port + offset
        try:
            app = web.Application()
            app.router.add_get('/health', health_check)
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
# 7. 메인 함수
# ============================================================
async def main() -> None:
    global _kiwoom, _monitor, _db, _start_time, _error_sender, _scheduler, _worker_tasks, _main_loop, _last_data_time, _health_task

    if not is_trading_day():
        log_event("NON_TRADING_DAY", {"date": datetime.now().strftime("%Y-%m-%d")})
        logger.info("📅 오늘은 비거래일입니다. 프로그램을 종료합니다.")
        debug_tower.log("SYSTEM", "NON_TRADING_DAY", {})
        return

    _main_loop = asyncio.get_running_loop()
    _last_data_time = time.time()

    log_event("SYSTEM_START", {"pid": os.getpid(), "version": "v7.2.6"})
    debug_tower.log("SYSTEM", "MAIN_START", {"pid": os.getpid(), "version": "v7.2.6"})

    check_and_create_pid()
    load_dotenv(override=True)
    validate_env()

    _start_time = asyncio.get_event_loop().time()
    _error_sender = TelegramSender()

    logger.info("=" * 70)
    logger.info("🚀 v7.2.6 FINAL - 실시간 모멘텀 스캔 루프 추가")
    logger.info("📌 기능: 6가지 이벤트(SIGNAL, SL_TRAIL, ATR_SPIKE, TP_HIT, EXIT, LIFECYCLE_ADVICE)")
    logger.info("=" * 70)

    startup_success = False
    startup_details: Dict[str, Any] = {}

    try:
        _db = DatabaseManager()
        await _db.init_db()
        logger.info("✅ DB 초기화 완료")
        log_event("DB_INIT_SUCCESS", {})
        debug_tower.log("SYSTEM", "DB_INIT_SUCCESS", {})

        _kiwoom = KiwoomConnectorV512(rate_limit=config.get_float("rate_limit_capacity", 5.0))
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
        startup_details['ticker_count'] = _monitor.get_subscribed_count()
        startup_details['kiwoom_connected'] = _kiwoom.is_connected()
        startup_details['tickers'] = _monitor.tickers
        log_event("MONITOR_STARTED", {"count": startup_details['ticker_count']})
        debug_tower.log("SYSTEM", "MONITOR_STARTED", {"count": startup_details['ticker_count']})

        analyzer = DeepAnalyzer(db_manager=_db)
        await analyzer.load_weights()
        sender = TelegramSender()

        daily_reporter = DailyReportGenerator(db_manager=_db, telegram_sender=sender)
        weekly_pdf_gen = WeeklyPDFGenerator(db_manager=_db, kiwoom_connector=_kiwoom)
        feedback_learner = FeedbackLearner(kiwoom_connector=_kiwoom, db_manager=_db)

        _scheduler = SchedulerManager()
        _scheduler.add_job_with_retry(
            run_daily_report,
            CronTrigger(hour=config.get_int("daily_report_hour", 7), minute=config.get_int("daily_report_minute", 0), timezone="Asia/Seoul"),
            "daily_report",
            daily_reporter,
            max_retries=3,
            retry_delay=5
        )
        _scheduler.add_job_with_retry(
            run_feedback_and_reload,
            CronTrigger(hour=config.get_int("feedback_hour", 17), minute=config.get_int("feedback_minute", 0), timezone="Asia/Seoul"),
            "feedback_learning",
            feedback_learner,
            analyzer,
            max_retries=3,
            retry_delay=5
        )
        _scheduler.add_job_with_retry(
            run_weekly_pdf,
            CronTrigger(day_of_week=config.get("weekly_pdf_day", "mon"), hour=config.get_int("weekly_pdf_hour", 6), minute=config.get_int("weekly_pdf_minute", 0), timezone="Asia/Seoul"),
            "weekly_pdf",
            weekly_pdf_gen,
            max_retries=3,
            retry_delay=5
        )
        _scheduler.add_job_with_retry(
            run_daily_ohlcv_collect,
            CronTrigger(hour=config.get_int("ohlcv_hour", 16), minute=config.get_int("ohlcv_minute", 30), timezone="Asia/Seoul"),
            "daily_ohlcv",
            _kiwoom,
            _db,
            _monitor.tickers,
            max_retries=3,
            retry_delay=5
        )
        _scheduler.start()
        startup_details['job_count'] = 4
        logger.info(f"⏰ 스케줄러 등록 완료 (총 {startup_details['job_count']}개 작업)")
        log_event("SCHEDULER_STARTED", {"jobs": startup_details['job_count']})
        debug_tower.log("SYSTEM", "SCHEDULER_STARTED", {"jobs": startup_details['job_count']})

        _worker_tasks = []
        for i in range(2):
            task = asyncio.create_task(strategy_worker(i+1, analyzer, _db, sender))
            _worker_tasks.append(task)

        _health_task = asyncio.create_task(start_health_server())

        startup_success = True
        await send_startup_notification(True, startup_details)
        log_event("SYSTEM_READY", {})
        debug_tower.log("SYSTEM", "SYSTEM_READY", {})

        logger.info("🚀 메인 루프 진입 (Phoenix Watchdog 활성화)")
        while True:
            try:
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

                # ============================================================
                # 🔥🔥🔥 수정 핵심: 1초마다 모멘텀 신호 스캔 (추가된 부분)
                # ============================================================
                signals = await _monitor.scan()
                for signal in signals:
                    try:
                        MESSAGE_QUEUE.put_nowait(signal)
                        debug_tower.log(signal.get('ticker'), "SIGNAL_ENQUEUED", {"action": signal.get('action')})
                    except asyncio.QueueFull:
                        logger.warning(f"⚠️ 큐 가득 참, 신호 드롭: {signal.get('ticker')}")
                        debug_tower.log(signal.get('ticker'), "SIGNAL_DROPPED", {"reason": "queue_full"})

                await asyncio.sleep(1)

            except asyncio.CancelledError:
                log_event("MAIN_LOOP_CANCELLED", {})
                debug_tower.log("SYSTEM", "MAIN_LOOP_CANCELLED", {})
                break
            except Exception as e:
                log_error("메인 루프 오류", e)
                debug_tower.capture_snapshot("SYSTEM", e, "MAIN_LOOP")
                await asyncio.sleep(5)

    except (KeyboardInterrupt, asyncio.CancelledError):
        log_event("SYSTEM_INTERRUPTED", {})
        logger.info("⏹ 종료 신호 수신")
        debug_tower.log("SYSTEM", "SYSTEM_INTERRUPTED", {})
    except FatalError as e:
        error_msg = f"치명적 오류: {str(e)}"
        log_error(error_msg, e)
        debug_tower.capture_snapshot("SYSTEM", e, "FATAL")
        startup_details['error'] = error_msg
        await send_startup_notification(False, startup_details)
        await send_error_alert(error_msg, traceback.format_exc()[:300])
        raise
    except Exception as e:
        error_msg = f"시작 실패: {str(e)}"
        log_error(error_msg, e)
        debug_tower.capture_snapshot("SYSTEM", e, "START_FAIL")
        startup_details['error'] = error_msg
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
            except:
                pass

        if _health_task and not _health_task.done():
            _health_task.cancel()
            try:
                await _health_task
            except asyncio.CancelledError:
                pass

        if _worker_tasks:
            for t in _worker_tasks:
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

        if _kiwoom:
            await _kiwoom.disconnect()
            await asyncio.sleep(0.5)

        if _scheduler:
            _scheduler.shutdown()

        debug_tower.flush()

        await asyncio.sleep(0.2)
        logger.info("✅ 시스템 안전하게 종료 완료")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 사용자 중단")
        debug_tower.flush()
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except:
                pass
    except Exception as e:
        print(f"❌ 시스템 종료: {e}")
        import traceback
        traceback.print_exc()
        debug_tower.flush()