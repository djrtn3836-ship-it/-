#!/usr/bin/env python3
"""
v5.2.0 ULTIMATE - 실시간 자율학습 스캐너 (통합 완결판)
- WebSocket 자동복구 + 재구독
- 일일 Telegram 브리프 (07:00)
- 주간 PDF 기관 보고서 (매주 월 06:00)
- 피드백 학습 및 가중치 EMA 업데이트 (17:00)
"""
import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, str(Path(__file__).parent))

from apscheduler.triggers.cron import CronTrigger

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

logger = setup_logger("scanner")


# ============================================================
# 1. 피드백 학습 + 가중치 재로드 통합 래퍼
# ============================================================
async def run_feedback_and_reload(learner: FeedbackLearner, analyzer: DeepAnalyzer):
    """17:00 실행: 학습 수행 후 분석기 메모리 캐시 갱신"""
    logger.info("🔄 17:00 정기 피드백 학습 및 가중치 동기화 시작...")
    await learner.run()
    await analyzer.load_weights()
    logger.info("✅ 피드백 학습 및 가중치 동기화 완료")


# ============================================================
# 2. 주간 PDF 생성 래퍼
# ============================================================
async def run_weekly_pdf(pdf_gen: WeeklyPDFGenerator):
    """매주 월 06:00 실행: PDF 생성 및 텔레그램 전송"""
    logger.info("📄 주간 PDF 보고서 생성 시작...")
    filepath = await pdf_gen.generate()
    if filepath:
        # Telegram으로 PDF 파일 전송 (선택)
        # sender = TelegramSender()
        # await sender.send_document(filepath)  # (구현 필요)
        logger.info(f"✅ PDF 생성 완료: {filepath}")
    else:
        logger.error("❌ PDF 생성 실패")


# ============================================================
# 3. WebSocket 재연결 + 구독 재등록 통합 함수
# ============================================================
async def reconnect_and_resubscribe(kiwoom: KiwoomConnectorV512, monitor: RealtimeMonitor):
    """연결 끊김 시 재연결 + 전체 종목 재구독"""
    logger.warning("🔄 WebSocket 연결 끊김 감지! 재연결 및 구독 재등록 시작...")
    
    retry_count = 0
    while not kiwoom.is_connected():
        retry_count += 1
        logger.info(f"📡 재연결 시도 중... ({retry_count}회차)")
        await kiwoom.connect()
        if not kiwoom.is_connected():
            await asyncio.sleep(30)
    
    logger.info("✅ WebSocket 재연결 성공! 실시간 구독 재등록 진행 중...")
    await monitor.resubscribe_all()
    logger.info("✅ 재연결 및 전체 구독 재등록 완료.")


# ============================================================
# 4. 메인 함수
# ============================================================
async def main():
    logger.info("=" * 60)
    logger.info("v5.2.0 ULTIMATE - 통합 자율학습 퀀트 시스템 가동")
    logger.info("=" * 60)

    # --- 1) DB 초기화 ---
    db = DatabaseManager()
    await db.init_db()

    # --- 2) 키움 커넥터 (장 시작 전 무한 재시도) ---
    kiwoom = KiwoomConnectorV512()
    logger.info("⏳ 키움 서버 연결 대기 중 (장 시작 전이면 60초 후 재시도)...")
    while not kiwoom.is_connected():
        await kiwoom.connect()
        if not kiwoom.is_connected():
            logger.info("⏳ 서버 연결 실패 (장 종료 또는 네트워크 문제). 60초 후 재시도...")
            await asyncio.sleep(60)
    logger.info("✅ 키움 서버 연결 성공!")

    # --- 3) 실시간 모니터 (구독 시작) ---
    monitor = RealtimeMonitor(kiwoom)
    await monitor.start()

    # --- 4) 심층 분석기 (가중치 로드) ---
    analyzer = DeepAnalyzer(db_manager=db)
    await analyzer.load_weights()

    # --- 5) 텔레그램 및 리포트 생성기 ---
    sender = TelegramSender()
    daily_reporter = DailyReportGenerator(db_manager=db, telegram_sender=sender)
    weekly_pdf_gen = WeeklyPDFGenerator(db_manager=db, kiwoom_connector=kiwoom)
    feedback_learner = FeedbackLearner(kiwoom_connector=kiwoom, db_manager=db)

    # --- 6) 스케줄러 등록 ---
    scheduler = SchedulerManager()

    # ✅ 매일 07:00 데일리 브리프 (Telegram)
    scheduler.add_daily_report(daily_reporter.generate_and_send, hour=7, minute=0)

    # ✅ 매일 17:00 피드백 학습 + 가중치 동기화
    scheduler.add_feedback_learning(
        lambda: run_feedback_and_reload(feedback_learner, analyzer),
        hour=17,
        minute=0
    )

    # ✅ 매주 월요일 06:00 주간 PDF 보고서 생성
    scheduler.scheduler.add_job(
        lambda: asyncio.create_task(run_weekly_pdf(weekly_pdf_gen)),
        trigger=CronTrigger(day_of_week='mon', hour=6, minute=0, timezone="Asia/Seoul"),
        id="weekly_pdf",
        replace_existing=True
    )
    logger.info("⏰ 스케줄러 등록 완료 (07:00 브리프, 17:00 학습, 월 06:00 PDF)")

    scheduler.start()

    # --- 7) 메인 실시간 루프 ---
    logger.info("🚀 실시간 스캔 루프 진입. 연결이 끊겨도 자동 복구됩니다.")
    try:
        while True:
            # 🔥 연결 상태 체크 (자동복구)
            if not kiwoom.is_connected():
                await reconnect_and_resubscribe(kiwoom, monitor)
                await asyncio.sleep(1)
                continue

            # 정상 스캔
            detected = await monitor.scan()
            if detected:
                for stock in detected:
                    analysis = await analyzer.analyze(stock)
                    await db.save_decision(analysis)
                    await sender.send(analysis)
                    logger.info(f"📊 신호 발생: {stock.get('ticker')} ({analysis.get('action')})")
            
            await asyncio.sleep(1)

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("⏹ 종료 신호 수신")
    finally:
        scheduler.shutdown()
        await kiwoom.disconnect()
        logger.info("✅ 시스템 안전하게 종료 완료")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("스캐너 종료 (KeyboardInterrupt)")