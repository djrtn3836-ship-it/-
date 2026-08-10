#!/usr/bin/env python3
"""
v5.1.2 FINAL - 실시간 자율학습 스캐너 (자동복구 + 구독 재등록 완벽 지원)
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

# ============================================================
# 글로벌 설정 (추후 config 또는 universe에서 자동 로드 가능)
# ============================================================
# 🔥 현재 모니터링할 종목 리스트 (RealtimeMonitor와 동기화 필요)
ACTIVE_TICKERS = ["005930", "000660", "035420"]  # 예시: 삼성전자, SK하이닉스, NAVER

# ============================================================
# 피드백 학습 + 가중치 재로드 통합 래퍼
# ============================================================
async def run_feedback_and_reload(learner: FeedbackLearner, analyzer: DeepAnalyzer):
    """17:00에 실행: 학습 수행 후, 분석기의 메모리 캐시를 즉시 갱신"""
    logger.info("🔄 17:00 정기 피드백 학습 및 가중치 동기화 시작...")
    await learner.run()
    await analyzer.load_weights()
    logger.info("✅ 피드백 학습 및 가중치 동기화 완료")

# ============================================================
# WebSocket 재연결 + 구독 재등록 통합 함수 (🔥 핵심)
# ============================================================
async def reconnect_and_resubscribe(kiwoom: KiwoomConnectorV512, monitor: RealtimeMonitor):
    """
    WebSocket 연결이 끊겼을 때:
    1. 재연결을 시도하고,
    2. 성공하면 RealtimeMonitor가 알고 있는 모든 종목을 다시 REG(구독)합니다.
    """
    logger.warning("🔄 WebSocket 연결 끊김 감지! 재연결 및 구독 재등록 시작...")
    
    # 1. 재연결 시도 (최대 무한, 30초 간격)
    retry_count = 0
    while not kiwoom.is_connected():
        retry_count += 1
        logger.info(f"📡 재연결 시도 중... ({retry_count}회차)")
        await kiwoom.connect()
        if not kiwoom.is_connected():
            await asyncio.sleep(30)  # 30초 대기 후 재시도
    
    logger.info("✅ WebSocket 재연결 성공! 실시간 구독 재등록 진행 중...")
    
    # 2. 🔥 [가장 중요] 저장된 모든 종목 다시 구독 요청 (REG 재전송)
    await monitor.resubscribe_all()
    
    logger.info("✅ 재연결 및 전체 구독 재등록 완료. 정상 데이터 수신 재개.")

# ============================================================
# 메인 함수
# ============================================================
async def main():
    logger.info("=" * 60)
    logger.info("v5.1.2 FINAL - 실시간 자율학습 스캐너 가동 (자동복구 + 재구독 지원)")
    logger.info("=" * 60)

    # 1. DB 초기화
    db = DatabaseManager()
    await db.init_db()

    # 2. 키움 커넥터 생성 (아직 연결 안 함)
    kiwoom = KiwoomConnectorV512()
    
    # 3. 🚀 장 시작 전이라면 연결될 때까지 무한 대기 (60초 간격 재시도)
    logger.info("⏳ 키움 서버 연결 대기 중 (장 시작 전이면 60초 후 재시도)...")
    while not kiwoom.is_connected():
        await kiwoom.connect()
        if not kiwoom.is_connected():
            logger.info("⏳ 서버 연결 실패 (장 종료 또는 네트워크 문제). 60초 후 재시도...")
            await asyncio.sleep(60)
    logger.info("✅ 키움 서버 연결 성공!")

    # 4. 실시간 모니터 (구독은 내부에서 자동으로 REG 요청)
    monitor = RealtimeMonitor(kiwoom)
    await monitor.start()  # 이 안에서 ACTIVE_TICKERS 기반으로 REG 요청 전송

    # 5. 심층 분석기 (가중치 로드)
    analyzer = DeepAnalyzer(db_manager=db)
    await analyzer.load_weights()

    # 6. 텔레그램 및 스케줄러
    sender = TelegramSender()
    scheduler = SchedulerManager()

    daily_reporter = DailyReportGenerator(db_manager=db, telegram_sender=sender)
    feedback_learner = FeedbackLearner(kiwoom_connector=kiwoom, db_manager=db)

    scheduler.add_daily_report(daily_reporter.generate_and_send, hour=7, minute=0)
    scheduler.add_feedback_learning(
        lambda: run_feedback_and_reload(feedback_learner, analyzer),
        hour=17,
        minute=0
    )
    scheduler.start()

    logger.info("⏰ 스케줄러 등록 완료 (07:00 리포트, 17:00 학습 및 가중치 동기화)")
    logger.info("🚀 실시간 스캔 루프 진입. 연결이 끊겨도 자동 복구됩니다.")

    # 7. 메인 실시간 루프 (연결 상태 체크 + 자동복구)
    try:
        while True:
            # 🔥 [핵심] 연결 상태 체크: 끊어졌으면 재연결 + 재구독 실행
            if not kiwoom.is_connected():
                await reconnect_and_resubscribe(kiwoom, monitor)
                # 복구 후 1초 대기 후 재개 (버퍼 안정화)
                await asyncio.sleep(1)
                continue

            # 정상 연결 상태 -> 스캔 실행
            detected = await monitor.scan()
            if detected:
                for stock in detected:
                    analysis = await analyzer.analyze(stock)
                    await db.save_decision(analysis)
                    await sender.send(analysis)
                    logger.info(f"📊 신호 발생: {stock.get('ticker')} ({analysis.get('action')})")
            
            await asyncio.sleep(1)  # 1초 간격 스캔

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("⏹ 종료 신호 수신")
    finally:
        scheduler.shutdown()
        await kiwoom.disconnect()
        logger.info("✅ 시스템 안전하게 종료 완료")

if __name__ == "__main__":
    asyncio.run(main())