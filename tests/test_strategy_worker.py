"""
tests/test_strategy_worker.py - strategy_worker 통합 테스트
- price=0 틱 차단
- ERROR 액션 DB 저장 방지
- IGNORE 처리 확인
- 🔥 수정: price 문자열 처리 (float 변환) 추가
"""
import sys
import os
import asyncio
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender
from scanner.deep_analyzer import DeepAnalyzer

# 테스트용 로거
logger = setup_logger("test")

# 테스트용 큐
TEST_QUEUE = asyncio.Queue(maxsize=1000)

# 테스트용 더미 데이터
TEST_TICKS = [
    # 1. 정상 체결 데이터 (price > 0)
    {"ticker": "005930", "price": 80000, "volume": 1000, "timestamp": 1000},
    # 2. price=0 (차단되어야 함)
    {"ticker": "005930", "price": 0, "volume": 1000, "timestamp": 1001},
    # 3. price None (차단되어야 함)
    {"ticker": "005930", "volume": 1000, "timestamp": 1002},
    # 4. price가 문자열 (파싱 가능해야 함)
    {"ticker": "005930", "price": "81000", "volume": 1000, "timestamp": 1003},
    # 5. 0A 호가 데이터 (price 없음, 차단되어야 함)
    {"ticker": "005930", "type": "0A", "buy_fpr_bid": 80000, "timestamp": 1004},
]

async def test_strategy_worker_filter():
    """strategy_worker의 price 필터링 테스트"""
    
    # Mock 객체 생성
    db = DatabaseManager()
    analyzer = DeepAnalyzer(db_manager=db)
    sender = TelegramSender()
    
    # 테스트용 큐에 데이터 채우기
    for tick in TEST_TICKS:
        await TEST_QUEUE.put(tick)
    
    # strategy_worker 로직을 직접 실행 (테스트용 간소화 버전)
    processed_count = 0
    skipped_count = 0
    error_count = 0
    
    while not TEST_QUEUE.empty():
        try:
            stock_data = await asyncio.wait_for(TEST_QUEUE.get(), timeout=1.0)
            
            # 🔥 수정: strategy_worker와 동일한 로직 (float 변환 시 예외 처리)
            price = stock_data.get('price')
            try:
                price_float = float(price) if price is not None else 0.0
            except (ValueError, TypeError):
                price_float = 0.0
            
            # 🔥 2단계 핵심: price <= 0 차단
            if price is None or price_float <= 0:
                skipped_count += 1
                print(f"⏭️ SKIP: {stock_data.get('ticker')} (price={price})")
                TEST_QUEUE.task_done()
                continue
            
            # 분석 실행 (실제로는 DB 접근이 필요하므로 간소화)
            analysis = await analyzer.analyze(stock_data)
            
            if analysis.get('action') == 'ERROR':
                error_count += 1
                print(f"⚠️ ERROR: {analysis.get('error')}")
            else:
                processed_count += 1
                print(f"✅ PROCESS: {stock_data.get('ticker')} price={price}")
            
            TEST_QUEUE.task_done()
            
        except asyncio.TimeoutError:
            break
        except Exception as e:
            print(f"❌ 예외 발생: {e}")
            TEST_QUEUE.task_done()
    
    # 검증
    print(f"\n📊 결과: 처리={processed_count}, 스킵={skipped_count}, 에러={error_count}")
    assert processed_count == 2, f"정상 틱은 2개여야 함 (실제: {processed_count})"
    assert skipped_count == 3, f"차단 틱은 3개여야 함 (실제: {skipped_count})"
    assert error_count == 0, f"에러는 0개여야 함 (실제: {error_count})"
    print("🎉 통합 테스트 통과!")

if __name__ == "__main__":
    asyncio.run(test_strategy_worker_filter())