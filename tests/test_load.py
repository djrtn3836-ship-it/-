"""
tests/test_load.py - 부하 테스트 (장 시작 5분 시뮬레이션)
- 초당 200개 틱 생성 (0B + 0A 혼합)
- 큐 적재량, CPU 사용량 모니터링
- ZeroDivisionError 발생 여부 확인
"""

import sys
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import asyncio
import os
import random
import sys
import threading
import time

import psutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.logger import setup_logger
from scanner.realtime_monitor import RealtimeMonitor

logger = setup_logger("load_test")

# 테스트 설정
TICKERS = ["005930", "000660", "035420", "005380", "051910"]  # 5개만 테스트
DURATION_SECONDS = 60  # 1분간 폭주 (실제는 5분 권장)
TICKS_PER_SECOND = 200


async def generate_load():
    """부하 생성기"""
    start_time = time.time()
    total_ticks = 0
    zero_price_ticks = 0
    queue = asyncio.Queue(maxsize=100000)

    print(f"🚀 부하 테스트 시작: {TICKS_PER_SECOND}틱/초, {DURATION_SECONDS}초")

    # 모니터 객체 생성 (실제 WebSocket 없이 _on_data만 테스트)
    # 실제로는 KiwoomConnector가 필요하지만, 여기서는 _on_data만 직접 호출
    class MockKiwoom:
        def register_realtime(self, *args, **kwargs):
            pass

    monitor = RealtimeMonitor(MockKiwoom(), queue)

    # _on_data를 직접 호출하여 부하 생성
    async def generate_ticks():
        nonlocal total_ticks, zero_price_ticks
        end_time = start_time + DURATION_SECONDS
        tick_count = 0

        while time.time() < end_time:
            # 매 틱마다
            for _ in range(TICKS_PER_SECOND // 10):  # 0.1초 간격으로 분산
                ticker = random.choice(TICKERS)

                # 70%는 체결(0B), 30%는 호가(0A)
                if random.random() < 0.7:
                    # 체결 데이터
                    price = random.randint(50000, 200000)
                    # 5% 확률로 price=0 (파싱 실패 시뮬레이션)
                    if random.random() < 0.05:
                        price = 0
                        zero_price_ticks += 1

                    data = {
                        "ticker": ticker,
                        "price": price,
                        "volume": random.randint(100, 10000),
                        "type": "0B",
                        "timestamp": time.time(),
                    }
                else:
                    # 호가 데이터 (0A)
                    data = {
                        "ticker": ticker,
                        "type": "0A",
                        "buy_fpr_bid": random.randint(50000, 200000),
                        "sel_fpr_bid": random.randint(50000, 200000),
                        "timestamp": time.time(),
                    }

                monitor._on_data(data)
                total_ticks += 1
                tick_count += 1

            # 0.1초 대기
            await asyncio.sleep(0.1)

    # CPU 모니터링
    def monitor_cpu():
        while True:
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory().percent
            print(f"📊 CPU: {cpu}% | MEM: {mem}% | 큐: {queue.qsize()}")
            time.sleep(1)

    # 부하 생성 시작
    task = asyncio.create_task(generate_ticks())

    # CPU 모니터링 스레드 시작
    monitor_thread = threading.Thread(target=monitor_cpu, daemon=True)
    monitor_thread.start()

    # 1분간 실행
    await asyncio.sleep(DURATION_SECONDS)
    task.cancel()

    # 결과 집계
    print("\n📊 부하 테스트 완료!")
    print(f"   총 틱 생성: {total_ticks}")
    print(f"   price=0 틱: {zero_price_ticks}")
    print(f"   큐 최종 크기: {queue.qsize()}")
    print(f"   큐 최대 크기: {queue.maxsize}")

    # 큐에 남은 데이터 처리 (실제 워커가 처리할 데이터)
    processed = 0
    skipped = 0
    while not queue.empty():
        try:
            item = queue.get_nowait()
            price = item.get("price")
            if price is None or float(price) <= 0:
                skipped += 1
            else:
                processed += 1
            queue.task_done()
        except:
            break

    print(f"   큐 처리 결과: 처리={processed}, 스킵={skipped}")

    # 검증: ZeroDivisionError가 발생하지 않았는지 확인 (여기서는 직접 확인 불가)
    print("\n✅ 부하 테스트 완료! (ZeroDivisionError 발생 여부는 로그 확인)")


if __name__ == "__main__":
    asyncio.run(generate_load())
