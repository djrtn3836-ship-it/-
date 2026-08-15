#!/usr/bin/env python3
"""
scanner_main.py - v6.2.0 FINAL (이벤트 기반 액션 센터 + Phoenix 엔진)
- 전략 Worker가 5가지 이벤트(SIGNAL_ENTRY, SL_TRAIL, ATR_SPIKE, TP_HIT, EXIT)를 처리
- WebSocket 자가 치유, 블랙박스, 15:20 세이프가드 포함
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

# 🔥 FatalError를 Exception으로 대체
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
from config.secure_config import load_encrypted_env

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
PID_FILE = Path(__file__).parent / "scanner.pid"
MESSAGE_QUEUE: asyncio.Queue = asyncio.Queue(maxsize=config.get_int("queue_maxsize", 100000))

# 🔥 데이터 흐름 감시 변수 (Phoenix Watchdog)
_last_data_time = 0.0
_DATA_FLOW_TIMEOUT = 180  # 3분

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


# ============================================================
# 2. APScheduler 래퍼 (거래일 체크)
# ============================================================
async def trading_day_task_wrapper(func, job_name: str = "작업", *args, **kwargs) -> None:
    if not is_trading_day():
        logger.info(f"📅 오늘은 비거래일 → {job_name} 스킵")
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


async def reconnect_and_resubscribe(kiwoom: KiwoomConnectorV512, monitor: RealtimeMonitor) -> None:
    logger.warning("🔄 WebSocket 연결 끊김 감지! 재연결 및 구독 재등록 시작...")
    log_event("MAIN_RECONNECT_TRIGGERED", {})
    retry_count = 0
    while not kiwoom.is_connected():
        retry_count += 1
        logger.info(f"📡 재연결 시도 중... ({retry_count}회차)")
        await kiwoom.connect()
        if not kiwoom.is_connected():
            await asyncio.sleep(config.get_int("reconnect_interval", 30))
    logger.info("✅ WebSocket 재연결 성공! 재구독 진행 중...")
    await monitor.resubscribe_all()
    log_event("MAIN_RECONNECT_COMPLETE", {})
    logger.info("✅ 재연결 및 전체 구독 재등록 완료.")


# ============================================================
# 3. 🔥 v6.2.0 전략 Worker (이벤트 기반 처리)
# ============================================================
async def strategy_worker(worker_id: int, analyzer: DeepAnalyzer, db: DatabaseManager, sender: TelegramSender) -> None:
    global _last_data_time
    logger.info(f"🧠 전략 Worker-{worker_id} 시작 (v6.2.0 이벤트 기반)")
    processed_count = 0
    while True:
        try:
            try:
                stock_data = await asyncio.wait_for(MESSAGE_QUEUE.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # 🔥 데이터 도착 시간 갱신
            _last_data_time = time.time()

            # 분석 실행 (이벤트 발생 가능)
            analysis = await analyzer.analyze(stock_data)
            await db.save_decision(analysis)

            action = analysis.get('action')

            # 🔥 v6.2.0: 5가지 이벤트 기반 분할 전송
            if action in ["SIGNAL_ENTRY", "EVENT_SL_TRAIL", "EVENT_ATR_SPIKE", "EVENT_TP_HIT", "EVENT_EXIT"]:
                await sender.send(analysis)
                processed_count += 1

                # 이벤트별 로깅 및 후처리
                if action == "SIGNAL_ENTRY":
                    logger.info(f"📊 Worker-{worker_id} [진입] {analysis.get('ticker')} 신호 전송")
                elif action == "EVENT_SL_TRAIL":
                    logger.info(f"📊 Worker-{worker_id} [손절상승] {analysis.get('ticker')} {analysis.get('old_stop')} → {analysis.get('new_stop')}")
                elif action == "EVENT_ATR_SPIKE":
                    logger.info(f"📊 Worker-{worker_id} [ATR급변동] {analysis.get('ticker')} {analysis.get('atr_change_ratio', 0)*100:.0f}%")
                elif action == "EVENT_TP_HIT":
                    logger.info(f"📊 Worker-{worker_id} [부분익절] {analysis.get('ticker')} TP{analysis.get('tp_level')} 도달")
                elif action == "EVENT_EXIT":
                    logger.info(f"📊 Worker-{worker_id} [청산] {analysis.get('ticker')} 최종 손익 {analysis.get('pnl', 0):+.2f}%")
                    # 청산 시 트레일링 스탑 상태 제거
                    await analyzer.clear_trailing_stop(analysis.get('ticker'))

            if processed_count % 50 == 0 and processed_count > 0:
                logger.info(f"📊 Worker-{worker_id} 처리 완료: {processed_count}개 이벤트")

            MESSAGE_QUEUE.task_done()

        except asyncio.CancelledError:
            logger.info(f"🛑 전략 Worker-{worker_id} 종료")
            break
        except Exception as e:
            logger.error(f"❌ 전략 Worker-{worker_id} 오류: {e}", exc_info=True)
            await asyncio.sleep(1)


# ============================================================
# 4. Telegram 알림 함수 (시작/종료/오류)
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
📊 <b>버전</b>: v6.2.0 Phoenix (이벤트 기반 액션 센터)
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
# 5. 헬스체크 서버
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
        "blackbox": get_status()
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
            return
        except OSError:
            continue
    logger.warning("⚠️ 헬스체크 서버 시작 실패")


# ============================================================
# 6. 메인 함수
# ============================================================
async def main() -> None:
    global _kiwoom, _monitor, _db, _start_time, _error_sender, _scheduler, _worker_tasks, _main_loop, _last_data_time

    _main_loop = asyncio.get_running_loop()
    _last_data_time = time.time()

    log_event("SYSTEM_START", {"pid": os.getpid(), "version": "v6.2.0"})

    check_and_create_pid()
    load_encrypted_env()
    validate_env()

    _start_time = asyncio.get_event_loop().time()
    _error_sender = TelegramSender()

    logger.info("=" * 70)
    logger.info("🚀 v6.2.0 Phoenix - 이벤트 기반 액션 센터")
    logger.info("📌 기능: 5가지 이벤트(SIGNAL, SL_TRAIL, ATR_SPIKE, TP_HIT, EXIT)")
    logger.info("=" * 70)

    startup_success = False
    startup_details: Dict[str, Any] = {}

    try:
        # --- DB 초기화 ---
        _db = DatabaseManager()
        await _db.init_db()
        logger.info("✅ DB 초기화 완료")
        log_event("DB_INIT_SUCCESS", {})

        # --- 키움 연결 ---
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

        # --- WebSocket 준비 대기 ---
        logger.info("⏳ WebSocket LOGIN 및 수신 루프 준비 대기 중...")
        if not await _kiwoom.wait_until_ready(timeout=10.0):
            logger.warning("⚠️ WebSocket 준비 타임아웃, 재연결 시도")
            await _kiwoom.disconnect()
            await _kiwoom.connect()
            if not await _kiwoom.wait_until_ready(timeout=10.0):
                logger.error("❌ WebSocket 준비 실패")
                log_event("WS_READY_FAILED", {})
        else:
            logger.info("✅ WebSocket 완전 준비 완료")

        # --- RealtimeMonitor 시작 ---
        _monitor = RealtimeMonitor(_kiwoom, MESSAGE_QUEUE)
        await _monitor.start()
        startup_details['ticker_count'] = _monitor.get_subscribed_count()
        startup_details['kiwoom_connected'] = _kiwoom.is_connected()
        startup_details['tickers'] = _monitor.tickers
        log_event("MONITOR_STARTED", {"count": startup_details['ticker_count']})

        # --- 분석기 및 의존성 ---
        analyzer = DeepAnalyzer(db_manager=_db)
        await analyzer.load_weights()
        sender = TelegramSender()

        daily_reporter = DailyReportGenerator(db_manager=_db, telegram_sender=sender)
        weekly_pdf_gen = WeeklyPDFGenerator(db_manager=_db, kiwoom_connector=_kiwoom)
        feedback_learner = FeedbackLearner(kiwoom_connector=_kiwoom, db_manager=_db)

        # --- APScheduler 등록 (이벤트 루프 안전하게 실행) ---
        _scheduler = SchedulerManager()
        _scheduler.scheduler.add_job(
            lambda: asyncio.run_coroutine_threadsafe(run_daily_report(daily_reporter), _main_loop),
            trigger=CronTrigger(hour=config.get_int("daily_report_hour", 7), minute=config.get_int("daily_report_minute", 0), timezone="Asia/Seoul"),
            id="daily_report", replace_existing=True
        )
        _scheduler.scheduler.add_job(
            lambda: asyncio.run_coroutine_threadsafe(run_feedback_and_reload(feedback_learner, analyzer), _main_loop),
            trigger=CronTrigger(hour=config.get_int("feedback_hour", 17), minute=config.get_int("feedback_minute", 0), timezone="Asia/Seoul"),
            id="feedback_learning", replace_existing=True
        )
        _scheduler.scheduler.add_job(
            lambda: asyncio.run_coroutine_threadsafe(run_weekly_pdf(weekly_pdf_gen), _main_loop),
            trigger=CronTrigger(day_of_week=config.get("weekly_pdf_day", "mon"), hour=config.get_int("weekly_pdf_hour", 6), minute=config.get_int("weekly_pdf_minute", 0), timezone="Asia/Seoul"),
            id="weekly_pdf", replace_existing=True
        )
        _scheduler.scheduler.add_job(
            lambda: asyncio.run_coroutine_threadsafe(run_daily_ohlcv_collect(_kiwoom, _db, _monitor.tickers), _main_loop),
            trigger=CronTrigger(hour=config.get_int("ohlcv_hour", 16), minute=config.get_int("ohlcv_minute", 30), timezone="Asia/Seoul"),
            id="daily_ohlcv", replace_existing=True
        )
        _scheduler.start()
        startup_details['job_count'] = len(_scheduler.scheduler.get_jobs())
        logger.info(f"⏰ 스케줄러 등록 완료 (총 {startup_details['job_count']}개 작업)")
        log_event("SCHEDULER_STARTED", {"jobs": startup_details['job_count']})

        # --- 전략 Worker 시작 (2개) ---
        _worker_tasks = []
        for i in range(2):
            task = asyncio.create_task(strategy_worker(i+1, analyzer, _db, sender))
            _worker_tasks.append(task)

        # --- 헬스체크 서버 ---
        asyncio.create_task(start_health_server())

        # --- 시작 완료 알림 ---
        startup_success = True
        await send_startup_notification(True, startup_details)
        log_event("SYSTEM_READY", {})

        # --- 🔥 Phoenix 메인 루프 (데이터 흐름 감시 + 15:20 보호) ---
        logger.info("🚀 메인 루프 진입 (Phoenix Watchdog 활성화)")
        while True:
            try:
                # 1) 연결 상태 체크
                if not _kiwoom.is_connected():
                    await reconnect_and_resubscribe(_kiwoom, _monitor)
                    await asyncio.sleep(1)
                    continue

                # 2) 데이터 흐름 체크 (장중 9시~15시, 15:20 이후는 제외)
                now = datetime.now()
                if (9 <= now.hour <= 15) and not (now.hour == 15 and now.minute >= 20):
                    if time.time() - _last_data_time > _DATA_FLOW_TIMEOUT:
                        log_event("DATA_FLOW_TIMEOUT", {"seconds": _DATA_FLOW_TIMEOUT})
                        logger.error(f"🔥 데이터 흐름 감시: {_DATA_FLOW_TIMEOUT}초 동안 데이터 없음! 강제 재연결 시도")
                        await _kiwoom.disconnect()
                        await _kiwoom.connect()
                        await _monitor.resubscribe_all()
                        _last_data_time = time.time()

                await asyncio.sleep(1)
            except asyncio.CancelledError:
                log_event("MAIN_LOOP_CANCELLED", {})
                break
            except Exception as e:
                log_error("메인 루프 오류", e)
                await asyncio.sleep(5)

    except (KeyboardInterrupt, asyncio.CancelledError):
        log_event("SYSTEM_INTERRUPTED", {})
        logger.info("⏹ 종료 신호 수신")
    except FatalError as e:
        error_msg = f"치명적 오류: {str(e)}"
        log_error(error_msg, e)
        startup_details['error'] = error_msg
        await send_startup_notification(False, startup_details)
        await send_error_alert(error_msg, traceback.format_exc()[:300])
        raise
    except Exception as e:
        error_msg = f"시작 실패: {str(e)}"
        log_error(error_msg, e)
        startup_details['error'] = error_msg
        await send_startup_notification(False, startup_details)
        await send_error_alert(error_msg, traceback.format_exc()[:300])
        raise

    finally:
        # --- 종료 정리 ---
        if startup_success:
            await send_shutdown_notification("정상 종료")
        log_event("SYSTEM_SHUTDOWN", {})
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except:
                pass
        if _scheduler:
            _scheduler.shutdown()
        if _kiwoom:
            await _kiwoom.disconnect()
        if _worker_tasks:
            for t in _worker_tasks:
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except:
                        pass
        logger.info("✅ 시스템 안전하게 종료 완료")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 사용자 중단")
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except:
                pass
    except Exception as e:
        print(f"❌ 시스템 종료: {e}")