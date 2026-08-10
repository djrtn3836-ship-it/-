#!/usr/bin/env python3
"""
scanner_main.py - v5.3.2 ULTIMATE (헬스체크 + 암호화 + 재시도 통합)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from apscheduler.triggers.cron import CronTrigger
from aiohttp import web

from core.logger import setup_logger
from core.scheduler import SchedulerManager
from scanner.realtime_monitor import RealtimeMonitor
from scanner.deep_analyzer import DeepAnalyzer
from data.kiwoom_connector import KiwoomConnectorV512
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender
from report.daily_report import DailyReportGenerator
from report.weekly_pdf import WeeklyPDFGenerator
from feedback.feedback_learner import FeedbackLearner

# 🔥 D: 암호화 환경변수 로드
from config.secure_config import load_encrypted_env
load_encrypted_env()

logger = setup_logger("scanner")

# 글로벌 변수 (헬스체크용)
_kiwoom = None
_monitor = None
_db = None
_start_time = None

# ============================================================
# 1. 피드백 + 가중치 래퍼
# ============================================================
async def run_feedback_and_reload(learner: FeedbackLearner, analyzer: DeepAnalyzer):
    logger.info("🔄 17:00 정기 피드백 학습 및 가중치 동기화 시작...")
    await learner.run()
    await analyzer.load_weights()
    logger.info("✅ 피드백 학습 및 가중치 동기화 완료")

# ============================================================
# 2. 주간 PDF 래퍼
# ============================================================
async def run_weekly_pdf(pdf_gen: WeeklyPDFGenerator):
    logger.info("📄 주간 PDF 보고서 생성 시작...")
    try:
        filepath = await pdf_gen.generate()
        if filepath:
            logger.info(f"✅ PDF 생성 완료: {filepath}")
    except Exception as e:
        logger.error(f"❌ PDF 생성 오류: {e}")

# ============================================================
# 3. WebSocket 재연결 래퍼
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
# 4. 🔥 C: 헬스체크 API 핸들러
# ============================================================
async def health_check(request):
    global _kiwoom, _monitor, _db, _start_time
    status = {
        "status": "healthy",
        "uptime_seconds": (asyncio.get_event_loop().time() - _start_time) if _start_time else 0,
        "components": {
            "kiwoom": {
                "connected": _kiwoom.is_connected() if _kiwoom else False,
                "realtime_count": _kiwoom.get_realtime_count() if _kiwoom else 0,
            },
            "monitor": {
                "is_running": _monitor.is_running() if _monitor else False,
                "subscribed_count": _monitor.get_subscribed_count() if _monitor else 0,
            },
            "database": {
                "initialized": _db is not None,
            }
        }
    }
    if _db:
        try:
            await _db.get_weights()
            status["components"]["database"]["responsive"] = True
        except:
            status["components"]["database"]["responsive"] = False
            status["status"] = "degraded"
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
    except OSError as e:
        logger.warning(f"⚠️ 헬스체크 서버 시작 실패 (포트 {port} 사용 중): {e}")

# ============================================================
# 5. 메인 함수
# ============================================================
async def main():
    global _kiwoom, _monitor, _db, _start_time
    _start_time = asyncio.get_event_loop().time()

    logger.info("=" * 60)
    logger.info("v5.3.2 ULTIMATE - 통합 자율학습 퀀트 시스템 가동 (5대 개선 적용)")
    logger.info("호가잔량(0A) + 체결(0B) | DB 인덱싱 | 작업 재시도 | 헬스체크 | 암호화")
    logger.info("=" * 60)

    # --- 1) DB 초기화 ---
    _db = DatabaseManager()
    await _db.init_db()

    # --- 2) 키움 커넥터 ---
    _kiwoom = KiwoomConnectorV512()
    logger.info("⏳ 키움 서버 연결 대기 중 (장 시작 전이면 60초 후 재시도)...")
    while not _kiwoom.is_connected():
        await _kiwoom.connect()
        if not _kiwoom.is_connected():
            logger.info("⏳ 서버 연결 실패. 60초 후 재시도...")
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

    # --- 6) 스케줄러 (재시도 내장) ---
    scheduler = SchedulerManager()
    scheduler.add_daily_report(daily_reporter.generate_and_send, hour=7, minute=0)
    scheduler.add_feedback_learning(
        lambda: run_feedback_and_reload(feedback_learner, analyzer),
        hour=17, minute=0
    )
    # 주간 PDF 수동 등록 (재시도 적용)
    scheduler.scheduler.add_job(
        lambda: asyncio.create_task(scheduler._retry_wrapper(
            lambda: run_weekly_pdf(weekly_pdf_gen), 
            job_name="weekly_pdf"
        )),
        trigger=CronTrigger(day_of_week='mon', hour=6, minute=0, timezone="Asia/Seoul"),
        id="weekly_pdf",
        replace_existing=True
    )
    scheduler.start()
    logger.info("⏰ 스케줄러 등록 완료 (재시도 3회 적용)")

    # --- 7) 헬스체크 서버 (C) ---
    asyncio.create_task(start_health_server())

    # --- 8) 메인 루프 ---
    logger.info("🚀 실시간 스캔 루프 진입.")
    try:
        while True:
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

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("⏹ 종료 신호 수신")
    finally:
        scheduler.shutdown()
        await _kiwoom.disconnect()
        logger.info("✅ 시스템 안전하게 종료 완료")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("스캐너 종료")