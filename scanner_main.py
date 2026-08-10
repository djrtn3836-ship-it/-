#!/usr/bin/env python3
"""
v5.1.2 FINAL - 실시간 자율학습 스캐너 (Gemini 검증 + 캐시 동기화 패치 적용)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.logger import setup_logger
from core.scheduler import SchedulerManager
from scanner.realtime_monitor import RealtimeMonitor
from scanner.deep_analyzer import DeepAnalyzer
from data.kiwoom_connector import KiwoomConnectorV512
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender
from report.daily_report import DailyReportGenerator
from feedback.feedback_learner import FeedbackLearner

logger = setup_logger("scanner")

# 🔥 [신규] 피드백 학습 + 가중치 재로드 통합 래퍼 함수 (Gemini 지적 반영)
async def run_feedback_and_reload(learner: FeedbackLearner, analyzer: DeepAnalyzer):
    """17:00에 실행: 학습 수행 후, 분석기의 메모리 캐시를 즉시 갱신"""
    logger.info("🔄 17:00 정기 피드백 학습 및 가중치 동기화 시작...")
    await learner.run()                # 1. DB 업데이트 (feedback_weights)
    await analyzer.load_weights()      # 2. 메모리 캐시 재로드 (Stale Cache 해결)
    logger.info("✅ 피드백 학습 및 가중치 동기화 완료")

async def main():
    logger.info("=" * 60)
    logger.info("v5.1.2 FINAL - 실시간 자율학습 스캐너 가동")
    logger.info("=" * 60)

    # 1. DB 초기화
    db = DatabaseManager()
    await db.init_db()

    # 2. 키움 연결
    kiwoom = KiwoomConnectorV512()
    await kiwoom.connect()

    # 3. 실시간 모니터
    monitor = RealtimeMonitor(kiwoom)
    await monitor.start()

    # 4. 분석기 (초기 가중치 로드)
    analyzer = DeepAnalyzer(db_manager=db)
    await analyzer.load_weights()  # 시작 시 1회 로드

    # 5. Telegram 및 스케줄러
    sender = TelegramSender()
    scheduler = SchedulerManager()

    # 6. 일일 리포트 & 피드백 학습 인스턴스 생성
    daily_reporter = DailyReportGenerator(db_manager=db, telegram_sender=sender)
    feedback_learner = FeedbackLearner(kiwoom_connector=kiwoom, db_manager=db)

    # 7. 스케줄 등록
    scheduler.add_daily_report(daily_reporter.generate_and_send, hour=7, minute=0)
    
    # 🔥 [수정] 17:00에 학습 + 가중치 재로드 통합 콜백 등록
    scheduler.add_feedback_learning(
        lambda: run_feedback_and_reload(feedback_learner, analyzer),
        hour=17,
        minute=0
    )
    scheduler.start()

    logger.info("⏰ 스케줄러 등록 완료 (07:00 리포트, 17:00 학습 및 가중치 동기화)")

    # 8. 메인 실시간 루프
    try:
        while True:
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
    asyncio.run(main())