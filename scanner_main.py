#!/usr/bin/env python3
"""
scanner_main.py - v5.6.0 FINAL (설정 통합 + 수신/전략 분리)
- 모든 하드코딩 제거 → config/config.yaml + .env에서 로드
- 커스텀 예외 적용
- asyncio.Queue로 수신/전략 완전 분리
"""

import asyncio
import sys
import os
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from apscheduler.triggers.cron import CronTrigger
from aiohttp import web

from core.logger import setup_logger
from core.scheduler import SchedulerManager
from core.holiday_utils import is_trading_day
from core.config import get_config
from core.exceptions import KiwoomError, ConfigError
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
_kiwoom = None
_monitor = None
_db = None
_start_time = None
_error_sender = None
_scheduler = None
PID_FILE = Path(__file__).parent / "scanner.pid"

# 🔥 메시지 큐 (수신/전략 분리)
MESSAGE_QUEUE = asyncio.Queue(maxsize=config.get_int("queue_maxsize", 10000))

# ============================================================
# 1. 중복 실행 방지
# ============================================================
def check_and_create_pid():
    if PID_FILE.exists():
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            import subprocess
            result = subprocess.run(['tasklist', '/FI', f'PID eq {old_pid}'], 
                                   capture_output=True, text=True)
            if str(old_pid) in result.stdout:
                print(f"❌ 이미 실행 중인 프로세스가 있습니다 (PID: {old_pid})")
                sys.exit(1)
        except:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    print(f"✅ PID 파일 생성: {os.getpid()}")

# ============================================================
# 2. 환경변수 검증
# ============================================================
def validate_env():
    required_keys = ['KIWOOM_APP_KEY', 'KIWOOM_APP_SECRET', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID']
    missing = [k for k in required_keys if not os.getenv(k)]
    if missing:
        print(f"❌ 필수 환경변수가 없습니다: {', '.join(missing)}")
        print("   .env 파일을 확인하세요.")
        sys.exit(1)
    logger.info("✅ 환경변수 검증 완료")

# ============================================================
# 3. 공휴일 인식 래퍼
# ============================================================
async def trading_day_task_wrapper(func, job_name="작업", *args, **kwargs):
    if not is_trading_day():
        logger.info(f"📅 오늘은 비거래일 → {job_name} 스킵")
        return
    await func(*args, **kwargs)

# ============================================================
# 4. 주요 작업 래퍼
# ============================================================
async def run_feedback_and_reload(learner: FeedbackLearner, analyzer: DeepAnalyzer):
    await trading_day_task_wrapper(learner.run, "피드백 학습")
    await analyzer.load_weights()

async def run_weekly_pdf(pdf_gen: WeeklyPDFGenerator):
    await trading_day_task_wrapper(pdf_gen.generate, "주간 PDF")

async def run_daily_report(reporter: DailyReportGenerator):
    await trading_day_task_wrapper(reporter.generate_and_send, "일일 리포트")

async def run_daily_ohlcv_collect(kiwoom, db, tickers):
    await trading_day_task_wrapper(collect_daily_ohlcv, "OHLCV 수집", kiwoom, db, tickers)

# ============================================================
# 5. WebSocket 재연결
# ============================================================
async def reconnect_and_resubscribe(kiwoom: KiwoomConnectorV512, monitor: RealtimeMonitor):
    logger.warning("🔄 WebSocket 연결 끊김 감지! 재연결 및 구독 재등록 시작...")
    retry_count = 0
    while not kiwoom.is_connected():
        retry_count += 1
        logger.info(f"📡 재연결 시도 중... ({retry_count}회차)")
        await kiwoom.connect()
        if not kiwoom.is_connected():
            await asyncio.sleep(config.get_int("reconnect_interval", 30))
    logger.info("✅ WebSocket 재연결 성공! 재구독 진행 중...")
    await monitor.resubscribe_all()
    logger.info("✅ 재연결 및 전체 구독 재등록 완료.")

# ============================================================
# 6. 전략 Worker (큐 소비 전용)
# ============================================================
async def strategy_worker(analyzer: DeepAnalyzer, db: DatabaseManager, sender: TelegramSender):
    logger.info("🧠 전략 Worker 시작 (큐 소비 대기 중...)")
    processed_count = 0
    while True:
        try:
            stock_data = await MESSAGE_QUEUE.get()
            analysis = await analyzer.analyze(stock_data)
            await db.save_decision(analysis)
            await sender.send(analysis)
            processed_count += 1
            if processed_count % 100 == 0:
                logger.info(f"📊 처리 완료: {processed_count}개 신호")
            MESSAGE_QUEUE.task_done()
        except asyncio.CancelledError:
            logger.info("🛑 전략 Worker 종료")
            break
        except Exception as e:
            logger.error(f"❌ 전략 Worker 오류: {e}", exc_info=True)
            await asyncio.sleep(1)

# ============================================================
# 7. 오류 알림
# ============================================================
async def send_error_alert(error_msg: str, error_detail: str = ""):
    global _error_sender
    if _error_sender is None:
        _error_sender = TelegramSender()
    message = f"""
🚨 <b>시스템 치명적 오류</b>
━━━━━━━━━━━━━━━━━━━━━
📌 {error_msg}
📋 {error_detail[:200] if error_detail else '없음'}
🕒 {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
━━━━━━━━━━━━━━━━━━━━━
"""
    try:
        await _error_sender.send_raw(message)
    except:
        pass

# ============================================================
# 8. 시작 알림
# ============================================================
async def send_startup_notification(success: bool, details: dict = None):
    global _error_sender
    if _error_sender is None:
        _error_sender = TelegramSender()
    
    details = details or {}
    status_emoji = "🟢" if success else "🔴"
    status_text = "시작 성공 (Running)" if success else "시작 실패 (Failed)"
    
    msg = f"""
{status_emoji} <b>시스템 상태 보고</b>
━━━━━━━━━━━━━━━━━━━━━
📌 <b>상태</b>: {status_text}
🕒 <b>시간</b>: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🤖 <b>PID</b>: {os.getpid()}
"""
    if success:
        msg += f"""
📡 <b>구독 종목</b>: {details.get('ticker_count', 0)}개
🔌 <b>키움 연결</b>: {"✅ 연결됨" if details.get('kiwoom_connected') else "❌ 연결 실패"}
⏰ <b>스케줄러</b>: {details.get('job_count', 0)}개 작업 등록
📊 <b>버전</b>: v5.6.0 FINAL
━━━━━━━━━━━━━━━━━━━━━
<i>실시간 스캔 + 전략 Worker 정상 가동 중</i>
"""
    else:
        msg += f"""
📋 <b>실패 사유</b>: {details.get('error', '알 수 없음')}
━━━━━━━━━━━━━━━━━━━━━
<i>로그 파일을 확인하세요. (logs/scanner.log)</i>
"""
    try:
        await _error_sender.send_raw(msg)
    except Exception as e:
        logger.error(f"❌ 시작 알림 전송 실패: {e}")

# ============================================================
# 9. 종료 알림
# ============================================================
async def send_shutdown_notification(reason: str = "정상 종료"):
    global _error_sender
    if _error_sender is None:
        _error_sender = TelegramSender()
    msg = f"""
🟡 <b>시스템 종료</b>
━━━━━━━━━━━━━━━━━━━━━
📌 <b>사유</b>: {reason}
🕒 <b>시간</b>: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
🤖 <b>PID</b>: {os.getpid()}
━━━━━━━━━━━━━━━━━━━━━
<i>시스템이 안전하게 종료되었습니다.</i>
"""
    try:
        await _error_sender.send_raw(msg)
    except:
        pass

# ============================================================
# 10. 헬스체크 API
# ============================================================
async def health_check(request):
    global _kiwoom, _monitor, _db, _start_time
    status = {
        "status": "healthy",
        "uptime_seconds": (asyncio.get_event_loop().time() - _start_time) if _start_time else 0,
        "components": {
            "kiwoom": {"connected": _kiwoom.is_connected() if _kiwoom else False},
            "monitor": {"is_running": _monitor.is_running() if _monitor else False},
            "database": {"initialized": _db is not None},
            "queue_size": MESSAGE_QUEUE.qsize(),
        }
    }
    return web.json_response(status)

async def start_health_server(host='0.0.0.0', port=8080):
    app = web.Application()
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
        logger.info(f"🩺 헬스체크 서버 실행 중: http://{host}:{port}/health")
    except Exception as e:
        logger.warning(f"⚠️ 헬스체크 서버 시작 실패: {e}")

# ============================================================
# 11. 메인 함수
# ============================================================
async def main():
    global _kiwoom, _monitor, _db, _start_time, _error_sender, _scheduler
    
    check_and_create_pid()
    load_encrypted_env()
    validate_env()
    
    _start_time = asyncio.get_event_loop().time()
    _error_sender = TelegramSender()

    logger.info("=" * 60)
    logger.info("v5.6.0 FINAL - 통합 퀀트 시스템 가동")
    logger.info("설정: config/config.yaml + .env | 수신/전략 분리 적용")
    logger.info("=" * 60)

    startup_success = False
    startup_details = {}

    try:
        # --- 1) DB ---
        _db = DatabaseManager()
        await _db.init_db()
        logger.info("✅ DB 초기화 완료")

        # --- 2) 키움 ---
        _kiwoom = KiwoomConnectorV512(
            rate_limit=config.get_float("rate_limit_capacity", 5.0)
        )
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

        # --- 3) 모니터 ---
        _monitor = RealtimeMonitor(_kiwoom, MESSAGE_QUEUE)
        await _monitor.start()
        startup_details['ticker_count'] = _monitor.get_subscribed_count()
        startup_details['kiwoom_connected'] = _kiwoom.is_connected()

        # --- 4) 분석기 ---
        analyzer = DeepAnalyzer(db_manager=_db)
        await analyzer.load_weights()

        # --- 5) Telegram ---
        sender = TelegramSender()

        # --- 6) 리포트 생성기 ---
        daily_reporter = DailyReportGenerator(db_manager=_db, telegram_sender=sender)
        weekly_pdf_gen = WeeklyPDFGenerator(db_manager=_db, kiwoom_connector=_kiwoom)
        feedback_learner = FeedbackLearner(kiwoom_connector=_kiwoom, db_manager=_db)

        # --- 7) 스케줄러 ---
        _scheduler = SchedulerManager()
        
        # 일일 리포트
        _scheduler.scheduler.add_job(
            lambda: asyncio.create_task(run_daily_report(daily_reporter)),
            trigger=CronTrigger(
                hour=config.get_int("daily_report_hour", 7),
                minute=config.get_int("daily_report_minute", 0),
                timezone="Asia/Seoul"
            ),
            id="daily_report",
            replace_existing=True
        )
        
        # 피드백 학습
        _scheduler.scheduler.add_job(
            lambda: asyncio.create_task(run_feedback_and_reload(feedback_learner, analyzer)),
            trigger=CronTrigger(
                hour=config.get_int("feedback_hour", 17),
                minute=config.get_int("feedback_minute", 0),
                timezone="Asia/Seoul"
            ),
            id="feedback_learning",
            replace_existing=True
        )
        
        # 주간 PDF
        _scheduler.scheduler.add_job(
            lambda: asyncio.create_task(run_weekly_pdf(weekly_pdf_gen)),
            trigger=CronTrigger(
                day_of_week=config.get("weekly_pdf_day", "mon"),
                hour=config.get_int("weekly_pdf_hour", 6),
                minute=config.get_int("weekly_pdf_minute", 0),
                timezone="Asia/Seoul"
            ),
            id="weekly_pdf",
            replace_existing=True
        )
        
        # OHLCV 수집
        _scheduler.scheduler.add_job(
            lambda: asyncio.create_task(run_daily_ohlcv_collect(_kiwoom, _db, _monitor.tickers)),
            trigger=CronTrigger(
                hour=config.get_int("ohlcv_hour", 16),
                minute=config.get_int("ohlcv_minute", 30),
                timezone="Asia/Seoul"
            ),
            id="daily_ohlcv",
            replace_existing=True
        )
        
        _scheduler.start()
        job_count = len(_scheduler.scheduler.get_jobs())
        startup_details['job_count'] = job_count
        logger.info("⏰ 스케줄러 등록 완료")

        # --- 8) 전략 Worker ---
        worker_task = asyncio.create_task(strategy_worker(analyzer, _db, sender))

        # --- 9) 헬스체크 ---
        asyncio.create_task(start_health_server())

        # --- 10) 시작 알림 ---
        startup_success = True
        await send_startup_notification(True, startup_details)

        # --- 11) 메인 루프 ---
        logger.info("🚀 메인 루프 진입 (연결 상태 감시)")
        while True:
            try:
                if not _kiwoom.is_connected():
                    await reconnect_and_resubscribe(_kiwoom, _monitor)
                    await asyncio.sleep(1)
                    continue
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"⚠️ 메인 루프 오류: {e}")
                await asyncio.sleep(5)

    except Exception as e:
        error_msg = f"시작 실패: {str(e)}"
        logger.error(error_msg, exc_info=True)
        startup_details['error'] = error_msg
        await send_startup_notification(False, startup_details)
        await send_error_alert(error_msg, traceback.format_exc()[:300])
        raise

    finally:
        if startup_success:
            await send_shutdown_notification("정상 종료")
        if PID_FILE.exists():
            PID_FILE.unlink()
        if _scheduler:
            _scheduler.shutdown()
        if _kiwoom:
            await _kiwoom.disconnect()
        logger.info("✅ 시스템 종료")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("사용자 종료")
        if PID_FILE.exists():
            PID_FILE.unlink()
    except Exception as e:
        print(f"❌ 시스템 종료: {e}")