#!/usr/bin/env python3
"""
scanner_main.py - v5.4.4 ULTIMATE (PID + 환경변수 + 공휴일 인식 + DB 효율화)
- PID 중복 실행 방지
- 환경변수 사전 검증
- 공휴일/주말 자동 스킵 (pytimekr 연동)
- 피드백 학습 DB 조회로 변경 (API 낭비 제거)
- 모든 APScheduler 작업에 거래일 래퍼 적용
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

# --- 글로벌 변수 ---
_kiwoom = None
_monitor = None
_db = None
_start_time = None
_error_sender = None
_scheduler = None  # 🔥 finally 블록을 위해 전역화
PID_FILE = Path(__file__).parent / "scanner.pid"

# ============================================================
# 1. 중복 실행 방지 (PID 파일)
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
# 2. 환경변수 사전 검증
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
# 3. 공휴일 인식 작업 래퍼
# ============================================================
async def trading_day_task_wrapper(func, job_name="작업", *args, **kwargs):
    """거래일에만 실행되도록 보장하는 래퍼"""
    if not is_trading_day():
        logger.info(f"📅 오늘은 비거래일 (주말/공휴일) → {job_name} 스킵")
        return
    logger.info(f"📊 거래일 확인 완료 → {job_name} 실행")
    await func(*args, **kwargs)

# ============================================================
# 4. 주요 작업 래퍼 함수들
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
            await asyncio.sleep(30)
    logger.info("✅ WebSocket 재연결 성공! 재구독 진행 중...")
    await monitor.resubscribe_all()
    logger.info("✅ 재연결 및 전체 구독 재등록 완료.")

# ============================================================
# 6. 오류 알림
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
# 7. 헬스체크 API
# ============================================================
async def health_check(request):
    global _kiwoom, _monitor, _db, _start_time
    status = {
        "status": "healthy",
        "uptime_seconds": (asyncio.get_event_loop().time() - _start_time) if _start_time else 0,
        "components": {
            "kiwoom": {"connected": _kiwoom.is_connected() if _kiwoom else False},
            "monitor": {"is_running": _monitor.is_running() if _monitor else False},
            "database": {"initialized": _db is not None}
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
# 8. 메인 함수
# ============================================================
async def main():
    global _kiwoom, _monitor, _db, _start_time, _error_sender, _scheduler
    
    # --- 사전 검증 ---
    check_and_create_pid()
    load_encrypted_env()
    validate_env()
    
    _start_time = asyncio.get_event_loop().time()
    _error_sender = TelegramSender()

    logger.info("=" * 60)
    logger.info("v5.4.4 - 시스템 가동 (공휴일 인식 + PID + DB 효율화)")
    logger.info("=" * 60)

    try:
        # --- 1) DB 초기화 ---
        _db = DatabaseManager()
        await _db.init_db()
        logger.info("✅ DB 초기화 완료")

        # --- 2) 키움 커넥터 ---
        _kiwoom = KiwoomConnectorV512()
        logger.info("⏳ 키움 서버 연결 대기 중...")
        retry_count = 0
        while not _kiwoom.is_connected():
            retry_count += 1
            await _kiwoom.connect()
            if not _kiwoom.is_connected():
                if retry_count % 5 == 0:
                    await send_error_alert(f"키움 연결 실패 (재시도 {retry_count}회)")
                logger.info(f"⏳ {retry_count}회차 실패, 60초 후 재시도...")
                await asyncio.sleep(60)
        logger.info("✅ 키움 서버 연결 성공!")

        # --- 3) 실시간 모니터 ---
        _monitor = RealtimeMonitor(_kiwoom)
        await _monitor.start()

        # --- 4) 분석기 ---
        analyzer = DeepAnalyzer(db_manager=_db)
        await analyzer.load_weights()

        # --- 5) 텔레그램/리포트 ---
        sender = TelegramSender()
        daily_reporter = DailyReportGenerator(db_manager=_db, telegram_sender=sender)
        weekly_pdf_gen = WeeklyPDFGenerator(db_manager=_db, kiwoom_connector=_kiwoom)
        feedback_learner = FeedbackLearner(kiwoom_connector=_kiwoom, db_manager=_db)

        # --- 6) 스케줄러 (전체 작업에 거래일 래퍼 적용) ---
        _scheduler = SchedulerManager()
        
        # 매일 07:00 일일 리포트 (거래일만)
        _scheduler.scheduler.add_job(
            lambda: asyncio.create_task(run_daily_report(daily_reporter)),
            trigger=CronTrigger(hour=7, minute=0, timezone="Asia/Seoul"),
            id="daily_report",
            replace_existing=True
        )
        
        # 매일 17:00 피드백 학습 (거래일만)
        _scheduler.scheduler.add_job(
            lambda: asyncio.create_task(run_feedback_and_reload(feedback_learner, analyzer)),
            trigger=CronTrigger(hour=17, minute=0, timezone="Asia/Seoul"),
            id="feedback_learning",
            replace_existing=True
        )
        
        # 매주 월요일 06:00 주간 PDF (거래일만)
        _scheduler.scheduler.add_job(
            lambda: asyncio.create_task(run_weekly_pdf(weekly_pdf_gen)),
            trigger=CronTrigger(day_of_week='mon', hour=6, minute=0, timezone="Asia/Seoul"),
            id="weekly_pdf",
            replace_existing=True
        )
        
        # 매일 16:30 OHLCV 수집 (거래일만)
        _scheduler.scheduler.add_job(
            lambda: asyncio.create_task(run_daily_ohlcv_collect(_kiwoom, _db, _monitor.tickers)),
            trigger=CronTrigger(hour=16, minute=30, timezone="Asia/Seoul"),
            id="daily_ohlcv",
            replace_existing=True
        )
        
        _scheduler.start()
        logger.info("⏰ 스케줄러 등록 완료 (모든 작업 거래일/공휴일 인식 적용)")

        # --- 7) 헬스체크 ---
        asyncio.create_task(start_health_server())

        # --- 8) 메인 루프 ---
        logger.info("🚀 실시간 스캔 루프 진입.")
        while True:
            try:
                if not _kiwoom.is_connected():
                    await reconnect_and_resubscribe(_kiwoom, _monitor)
                    await asyncio.sleep(1)
                    continue

                detected = await _monitor.scan()
                if detected:
                    for stock in detected:
                        analysis = await analyzer.analyze(stock)
                        await _db.save_decision(analysis)
                        await sender.send(analysis)
                        logger.info(f"📊 신호 발생: {stock.get('ticker')} ({analysis.get('action')})")
                
                await asyncio.sleep(1)
            except Exception as e:
                error_msg = f"스캔 루프 오류: {str(e)}"
                logger.error(error_msg, exc_info=True)
                await send_error_alert(error_msg, traceback.format_exc()[:300])
                await asyncio.sleep(10)

    except Exception as e:
        error_msg = f"시작 실패: {str(e)}"
        logger.error(error_msg, exc_info=True)
        await send_error_alert(error_msg, traceback.format_exc()[:300])
        raise

    finally:
        # --- 정리 ---
        if PID_FILE.exists():
            PID_FILE.unlink()
            logger.info("🗑️ PID 파일 삭제 완료")
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