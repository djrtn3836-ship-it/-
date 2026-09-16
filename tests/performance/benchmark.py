import time
import sys
import os

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/../../')

print("⏱️  Config Load 벤치마크:")
start = time.time()
from core.config import ConfigManager
config_times = []
for _ in range(100):
    cfg = ConfigManager()
    config_times.append((time.time() - start) * 1000)
avg_config = sum(config_times) / len(config_times)
print(f"  평균: {avg_config:.2f}ms")
print(f"  최소: {min(config_times):.2f}ms, 최대: {max(config_times):.2f}ms")

print("\n⏱️  Logger 벤치마크:")
start = time.time()
from core.logger import setup_logger
logger = setup_logger("benchmark_test")
logger_times = []
for _ in range(1000):
    logger.debug("test message")
    logger_times.append((time.time() - start) * 1000)
avg_logger = sum(logger_times) / len(logger_times)
print(f"  평균: {avg_logger:.3f}ms")
print(f"  최소: {min(logger_times):.3f}ms, 최대: {max(logger_times):.3f}ms")

print("\n✅ 벤치마크 완료")