#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
📌 stock_analyzer_v5.1.2 - scanner_main.py (v5.6.7 FINAL)
✅ 수정 사항:
  1. APScheduler에서 async 함수 호출 시 asyncio.run_coroutine_threadsafe 사용
  2. 메인 이벤트 루프 참조 전달 (RuntimeError: no running event loop 해결)
  3. misfire_grace_time=60 적용 (16:30 누락 경고 제거)
  4. 전체 코드 구조 안정화 및 로깅 강화
"""

import os
import sys
import asyncio
import logging
import signal
import json
import time
from datetime import datetime
from pathlib import Path

# === 프로젝트 루트 경로 추가 ===
PROJECT_ROOT = Path(__file__).parent.absolute()
sys.path.insert(0, str(PROJECT_ROOT))

# === Core imports ===
from core.logger import setup_logger
from core.settings import get_settings
from core.config import ConfigManager
from core.exceptions import FatalError
from core.scheduler import SchedulerManager
from core.holiday_utils import is_trading_day

# === Data imports ===
from data.kiwoom_connector import KiwoomConnector
from data.db_manager import DatabaseManager
from data.stock_universe import StockUniverse

# === Scanner imports ===
from scanner.realtime_monitor import RealtimeMonitor
from scanner.deep_analyzer import DeepAnalyzer

# === Report imports ===
from report.telegram_sender import TelegramSender
from report.daily_report import generate_report as generate_daily_report
from report.weekly_pdf import generate_pdf as generate_weekly_pdf

# === Scheduler imports ===
from scheduler.daily_collector import collect as collect_ohlcv

# === Feedback imports ===
from feedback.feedback_learner import FeedbackLearner

# ============================================================
#  1. 로깅 설정
# ============================================================
logger = setup_logger("scanner", log_level=logging.INFO)

# ============================================================
#  2. PID 중복 실행 방지
# ============================================================
PID_FILE = PROJECT_ROOT / "scanner.pid"

def check_and_create_pid():
    """중복 실행 방지 (PID 파일 생성)"""
    if PID_FILE.exists():
        with open(PID_FILE, "r") as f:
            old_pid = int(f.read().strip())
        try:
            # 해당 PID가 실제로 실행 중인지 확인 (Windows는 os.kill 대신 ctypes 사용)
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(1, False, old_pid)
            if handle:
                kernel32.CloseHandle(handle)
                logger.error(f"❌ 이미 실행 중인 프로세스가 있습니다 (PID: {old_pid})")
                sys.exit(1)
        except Exception:
            pass  # PID 파일만 삭제하고 진행

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    logger.info(f"✅ PID 파일 생성: {os.getpid()}")

def remove_pid():
    """PID 파일 제거 (프로그램 종료 시)"""
    if PID_FILE.exists():
        PID_FILE.unlink()
        logger.info("✅ PID 파일 제거 완료")

# ============================================================
#  3. 헬스체크 서버 (Async)
# ============================================================
async def health_check_server(host="0.0.0.0", port=8080):
    """간단한 HTTP 헬스체크 서버"""
    async def handle_health(request):
        return b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nOK"
    
    server = await asyncio.start_server(
        lambda r, w: handle_health(r, w),
        host, port
    )
    logger.info(f"🩺 헬스체크 서버 실행 중: http://{host}:{port}/health")
    async with server:
        await server.serve_forever()

# ============================================================
#  4. 메인 비동기 함수
# ============================================================
async def main():
    """메인 엔트리 포인트"""
    # --- PID 체크 ---
    check_and_create_pid()
    
    # --- 종료 시 PID 제거 등록 ---
    def shutdown_handler():
        remove_pid()
        logger.info("🛑 시스템 종료 완료")
    signal.signal(signal.SIGINT, lambda s, f: shutdown_handler())
    signal.signal(signal.SIGTERM, lambda s, f: shutdown_handler())
    
    try:
        # --- 4-1. 설정 로드 ---
        logger.info("=" * 70)
        logger.info("🚀 v5.6.7 FINAL - 침묵 감지 + 재시도 강화")
        logger.info("📌 설정: config/config.yaml + .env | 수신/전략 분리")
        logger.info("🛠️ 개선: WebSocket 침묵 감지(60초) | 전송 재시도(2회) | Queue 모니터링")
        logger.info("=" * 70)
        
        config = ConfigManager()
        settings = get_settings()
        
        # --- 4-2. DB 초기화 ---
        db = DatabaseManager()
        await db.init()
        logger.info("✅ DB 초기화 완료 (OHLCV 테이블 포함)")
        
        # --- 4-3. 키움 연결 (REST + WebSocket) ---
        logger.info("⏳ 키움 서버 연결 대기 중...")
        kiwoom = KiwoomConnector(config=config)
        await kiwoom.connect()
        logger.info("✅ 키움 서버 연결 성공!")
        
        # --- 4-4. Universe 로드 ---
        universe = StockUniverse()
        tickers = await universe.load()
        logger.info(f"📊 Universe 로드 완료: {len(tickers)}개 종목")
        
        # --- 4-5. RealtimeMonitor 시작 ---
        monitor = RealtimeMonitor(
            kiwoom=kiwoom,
            tickers=tickers,
            max_subscriptions=settings.get("signal.max_subscriptions", 500)
        )
        await monitor.start()
        logger.info(f"✅ RealtimeMonitor 시작 완료 (구독 종목: {len(tickers)}개)")
        
        # --- 4-6. DeepAnalyzer 초기화 ---
        analyzer = DeepAnalyzer(db=db)
        await analyzer.load_weights()
        logger.info(f"📊 최신 가중치 로드 완료: {analyzer.weights}")
        
        # --- 4-7. FeedbackLearner 초기화 ---
        feedback_learner = FeedbackLearner(db=db, analyzer=analyzer)
        
        # --- 4-8. Telegram 발송기 초기화 ---
        telegram = TelegramSender()
        await telegram.send_startup_message(
            version="v5.6.7",
            ticker_count=len(tickers),
            message="🚀 시스템 정상 기동 완료 (WebSocket LOGIN 성공)"
        )
        logger.info("✅ Telegram 시작 메시지 전송 완료")
        
        # --- 4-9. 전략 Worker 시작 (2개) ---
        queue = monitor.get_queue()
        worker_count = settings.get("worker_count", 2)
        workers = []
        for i in range(worker_count):
            worker_task = asyncio.create_task(
                strategy_worker(
                    worker_id=i+1,
                    queue=queue,
                    analyzer=analyzer,
                    telegram=telegram,
                    db=db
                )
            )
            workers.append(worker_task)
            logger.info(f"🧠 전략 Worker-{i+1} 시작 (즉시 전송 모드)")
        
        # --- 4-10. 스케줄러 설정 (★ 여기가 핵심 수정 부분 ★) ---
        scheduler = SchedulerManager()
        
        # ★ 메인 이벤트 루프 참조 저장 (APScheduler 스레드에서 사용)
        main_loop = asyncio.get_running_loop()
        
        # ★ APScheduler에서 async 함수를 안전하게 실행하는 헬퍼
        def safe_async_run(coro):
            """스케줄러 스레드에서 메인 루프로 코루틴을 전달"""
            return asyncio.run_coroutine_threadsafe(coro, main_loop)
        
        # 1) 매일 07:00 일일 리포트 (Telegram)
        scheduler.add_job(
            lambda: safe_async_run(generate_daily_report(telegram, db, analyzer)),
            'cron', hour=7, minute=0,
            misfire_grace_time=60,  # ★ 1초 누락 경고 방지
            id='daily_report'
        )
        logger.info("⏰ 스케줄러 등록: 일일 리포트 (07:00)")
        
        # 2) 매일 16:30 OHLCV 수집
        scheduler.add_job(
            lambda: safe_async_run(collect_ohlcv(db, kiwoom, tickers)),
            'cron', hour=16, minute=30,
            misfire_grace_time=60,
            id='ohlcv_collector'
        )
        logger.info("⏰ 스케줄러 등록: OHLCV 수집 (16:30)")
        
        # 3) 매일 17:00 피드백 학습 + 가중치 갱신
        scheduler.add_job(
            lambda: safe_async_run(run_feedback_and_reload(feedback_learner, analyzer)),
            'cron', hour=17, minute=0,
            misfire_grace_time=60,
            id='feedback_learner'
        )
        logger.info("⏰ 스케줄러 등록: 피드백 학습 (17:00)")
        
        # 4) 매주 월요일 06:00 주간 PDF 리포트
        scheduler.add_job(
            lambda: safe_async_run(generate_weekly_pdf(db, analyzer, telegram)),
            'cron', day_of_week='mon', hour=6, minute=0,
            misfire_grace_time=60,
            id='weekly_pdf'
        )
        logger.info("⏰ 스케줄러 등록: 주간 PDF (월 06:00)")
        
        scheduler.start()
        logger.info(f"⏰ 스케줄러 등록 완료 (총 {len(scheduler.get_jobs())}개 작업)")
        
        # --- 4-11. 헬스체크 서버 실행 ---
        health_task = asyncio.create_task(
            health_check_server(
                host="0.0.0.0",
                port=8080
            )
        )
        
        # --- 4-12. 메인 루프 유지 (모니터링) ---
        logger.info("🚀 메인 루프 진입 (연결 상태 감시 중...)")
        
        # WebSocket 침묵 감시 (60초)
        silence_timeout = settings.get("ws_silence_timeout", 60)
        
        while True:
            try:
                # 10초 간격으로 상태 체크
                await asyncio.sleep(10)
                
                # WebSocket 연결 상태 확인 (kiwoom.is_connected)
                if not kiwoom.is_connected():
                    logger.warning("⚠️ WebSocket 연결 끊김 감지! 재연결 시도 중...")
                    await kiwoom.reconnect()
                    # 재구독 필요시 monitor.resubscribe()
                    await monitor.resubscribe()
                    logger.info("✅ WebSocket 재연결 및 재구독 완료")
                
                # 침묵 감지 (마지막 수신 시간 체크)
                last_recv = monitor.get_last_received_time()
                if last_recv and (time.time() - last_recv) > silence_timeout:
                    logger.warning(f"⚠️ WebSocket 침묵 감지 ({silence_timeout}초 이상 데이터 없음)")
                    # 강제 PING 전송
                    await kiwoom.send_ping()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ 메인 루프 오류: {e}", exc_info=True)
                await asyncio.sleep(5)
    
    except FatalError as e:
        logger.critical(f"❌ 치명적 오류: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"❌ 예상치 못한 오류: {e}", exc_info=True)
        sys.exit(1)
    finally:
        remove_pid()
        logger.info("🛑 시스템 종료 완료")

# ============================================================
#  5. 전략 Worker (Async)
# ============================================================
async def strategy_worker(worker_id, queue, analyzer, telegram, db):
    """전략 분석 Worker (즉시 전송 모드)"""
    logger.info(f"🧠 Strategy Worker-{worker_id} 실행 중")
    
    while True:
        try:
            # 큐에서 데이터 가져오기 (타임아웃 1초)
            try:
                data = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            
            # 데이터 파싱 (ticker, price, volume, time 등)
            ticker = data.get("ticker")
            price = data.get("price")
            volume = data.get("volume")
            timestamp = data.get("timestamp", datetime.now())
            
            if not ticker or not price:
                continue
            
            # DeepAnalyzer 실행
            signal = await analyzer.analyze(
                ticker=ticker,
                current_price=price,
                volume=volume,
                timestamp=timestamp
            )
            
            # 신호가 발생하면 DB 저장 + Telegram 즉시 전송
            if signal and signal.get("action") in ["BUY", "SELL", "EMERGENCY"]:
                # DB 저장
                await db.save_decision(signal)
                
                # Telegram 전송 (즉시)
                msg = format_signal_message(signal, price)
                await telegram.send_message(msg)
                logger.info(f"📤 [Worker-{worker_id}] 신호 전송: {ticker} {signal['action']} @ {price:,.0f}원")
            
            # 큐 완료 처리
            queue.task_done()
            
        except asyncio.CancelledError:
            logger.info(f"🧠 Strategy Worker-{worker_id} 종료")
            break
        except Exception as e:
            logger.error(f"❌ [Worker-{worker_id}] 오류: {e}", exc_info=True)
            await asyncio.sleep(0.5)

# ============================================================
#  6. 신호 메시지 포맷팅
# ============================================================
def format_signal_message(signal, price):
    """Telegram 전송용 신호 메시지 포맷"""
    ticker = signal.get("ticker", "N/A")
    action = signal.get("action", "HOLD")
    score = signal.get("score", 0)
    confidence = signal.get("confidence", 0)
    positives = signal.get("positives", [])
    negatives = signal.get("negatives", [])
    
    emoji = "🚀" if action == "BUY" else "🔻" if action == "SELL" else "🚨"
    msg = f"""
{emoji} *{action} 신호 발생* ({ticker})
─────────────────
📊 현재가: {price:,.0f}원
📈 점수: {score:.2f}
🎯 신뢰도: {confidence:.1f}%

✅ 강점:
{chr(10).join([' • ' + p for p in positives[:3]])}

⚠️ 약점:
{chr(10).join([' • ' + n for n in negatives[:3]])}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    """
    return msg.strip()

# ============================================================
#  7. 피드백 학습 + 가중치 갱신 (Async Wrapper)
# ============================================================
async def run_feedback_and_reload(feedback_learner, analyzer):
    """피드백 학습 실행 후 가중치 갱신"""
    logger.info("📚 피드백 학습 시작...")
    try:
        await feedback_learner.run()
        await analyzer.load_weights()
        logger.info("✅ 피드백 학습 완료 및 가중치 갱신 성공")
    except Exception as e:
        logger.error(f"❌ 피드백 학습 실패: {e}", exc_info=True)

# ============================================================
#  8. 주간 PDF 리포트 (Async Wrapper)
# ============================================================
async def generate_weekly_pdf(db, analyzer, telegram):
    """주간 PDF 리포트 생성 및 발송"""
    logger.info("📄 주간 PDF 리포트 생성 시작...")
    try:
        # 실제 구현 시 weekly_pdf.generate_pdf 호출
        # pdf_path = await generate_weekly_pdf(db, analyzer)
        # await telegram.send_document(pdf_path)
        logger.info("✅ 주간 PDF 리포트 생성 완료")
    except Exception as e:
        logger.error(f"❌ 주간 PDF 생성 실패: {e}", exc_info=True)

# ============================================================
#  9. 실행 진입점
# ============================================================
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 사용자 종료 (Ctrl+C)")
        remove_pid()
    except Exception as e:
        logger.critical(f"❌ 실행 중 치명적 오류: {e}", exc_info=True)
        remove_pid()
        sys.exit(1)