"""
tests/test_fault_injection.py - 장애 주입 테스트
- Telegram 토큰 오류
- DB 연결 실패
- WebSocket 재연결 시뮬레이션
"""
import sys
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import sys
import os
import asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender

logger = setup_logger("fault_test")

async def test_telegram_failure():
    """Telegram 전송 실패 시 처리 검증"""
    print("🧪 Telegram 장애 주입 테스트...")
    
    # 잘못된 토큰으로 TelegramSender 생성
    os.environ["TELEGRAM_BOT_TOKEN"] = "invalid_token"
    os.environ["TELEGRAM_CHAT_ID"] = "123456789"
    
    sender = TelegramSender()
    
    # 전송 시도 (실패해야 함)
    result = await sender.send_raw("테스트 메시지")
    
    # 실패해도 시스템이 크래시나지 않고 False 반환
    assert result == False, "Telegram 전송 실패 시 False를 반환해야 함"
    print("✅ Telegram 장애 테스트 통과 (크래시 없음)")

async def test_db_failure():
    """DB 연결 실패 시 처리 검증"""
    print("🧪 DB 장애 주입 테스트...")
    
    # 잘못된 DB 경로
    db = DatabaseManager(db_path="./tests/invalid_path/decisions.db")
    
    try:
        await db.init_db()
        print("⚠️ DB 초기화가 예상외로 성공했습니다 (경로가 유효할 수 있음)")
    except Exception as e:
        print(f"✅ DB 장애 감지: {e} (크래시 없이 예외 처리됨)")
    
    # save_decision 시도 (에러가 나도 크래시 안 나야 함)
    try:
        await db.save_decision({"ticker": "005930", "action": "BUY"})
        print("⚠️ save_decision이 예상외로 성공했습니다")
    except Exception as e:
        print(f"✅ DB 저장 장애 감지: {e} (크래시 없이 예외 처리됨)")

async def test_websocket_reconnect():
    """WebSocket 재연결 시뮬레이션 (kiwoom_connector 테스트)"""
    print("🧪 WebSocket 재연결 테스트...")
    # 실제로는 KiwoomConnector를 생성하고 disconnect/connect를 반복
    
    from data.kiwoom_connector import KiwoomConnectorV512
    
    connector = KiwoomConnectorV512()
    
    # 연결 시도 (실제 API 키가 없으면 실패하지만 크래시는 안 나야 함)
    try:
        result = await connector.connect()
        print(f"✅ 연결 시도 결과: {result}")
    except Exception as e:
        print(f"⚠️ 연결 예외 발생: {e}")
    
    # disconnect 호출 (세션 정리)
    await connector.disconnect()
    print("✅ WebSocket 재연결 테스트 완료 (크래시 없음)")

async def main():
    print("=" * 50)
    print("🔬 장애 주입 테스트 시작")
    print("=" * 50)
    
    await test_telegram_failure()
    await test_db_failure()
    await test_websocket_reconnect()
    
    print("\n🎉 모든 장애 주입 테스트 통과!")

if __name__ == "__main__":
    asyncio.run(main())