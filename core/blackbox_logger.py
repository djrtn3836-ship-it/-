"""
core/blackbox_logger.py - v1.0 FINAL (시스템 블랙박스 / Flight Recorder)
- 모든 WebSocket Raw 데이터를 별도 파일에 저장
- 자동 로그 순환(Rotating File): 최대 10MB, 최대 5개 파일 유지 (자동 삭제)
- 시스템 상태 변화(연결, 재연결, 에러)를 상세 기록
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

# 블랙박스 저장 디렉토리
BLACKBOX_DIR = Path(__file__).parent.parent / "logs" / "blackbox"
BLACKBOX_DIR.mkdir(parents=True, exist_ok=True)

# 블랙박스 로거 설정 (Raw 데이터 전용)
blackbox_logger = logging.getLogger("BLACKBOX")
blackbox_logger.setLevel(logging.DEBUG)  # 가장 낮은 레벨로 설정 (모든 것 저장)

# 파일 핸들러 (자동 순환: 10MB 초과 시 백업, 최대 5개 파일)
log_file_path = BLACKBOX_DIR / "blackbox.log"
file_handler = RotatingFileHandler(
    log_file_path,
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5,              # 최대 5개 파일 (blackbox.log, blackbox.log.1 ~ .4)
    encoding='utf-8'
)

# 포맷: [시간] [레벨] [파일:줄번호] 메시지
formatter = logging.Formatter(
    '[%(asctime)s] [%(levelname)-8s] [%(filename)s:%(lineno)d] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler.setFormatter(formatter)

# 핸들러를 로거에 추가 (중복 추가 방지)
if not blackbox_logger.handlers:
    blackbox_logger.addHandler(file_handler)

# 콘솔에도 출력하고 싶다면 아래 주석 해제 (운영 시에는 비권장)
# console_handler = logging.StreamHandler()
# console_handler.setFormatter(formatter)
# blackbox_logger.addHandler(console_handler)


# ============================================================
# 🔥 편의 함수 (외부에서 쉽게 호출)
# ============================================================

def log_raw_data(data: str, source: str = "WEBSOCKET"):
    """WebSocket 등에서 수신한 원본 데이터를 그대로 저장"""
    # 데이터가 너무 길면 500자로 자르되, 전체 내용은 파일에 기록됨 (핸들러가 알아서 처리)
    blackbox_logger.debug(f"[RAW][{source}] {data}")

def log_event(event: str, details: dict = None):
    """시스템 이벤트(연결 성공, 재연결 시도 등) 기록"""
    if details:
        blackbox_logger.info(f"[EVENT] {event} | {details}")
    else:
        blackbox_logger.info(f"[EVENT] {event}")

def log_error(error_msg: str, error_obj: Exception = None):
    """오류 발생 시 상세 기록"""
    if error_obj:
        blackbox_logger.error(f"[ERROR] {error_msg} | Exception: {type(error_obj).__name__} - {str(error_obj)}")
    else:
        blackbox_logger.error(f"[ERROR] {error_msg}")

def log_performance(module: str, action: str, elapsed_ms: float):
    """성능 측정 (선택 사항)"""
    blackbox_logger.debug(f"[PERF] {module} | {action} | {elapsed_ms:.2f}ms")

# ============================================================
# 상태: 현재 블랙박스 파일 정보
# ============================================================
def get_status():
    """블랙박스 상태 확인 (파일 크기, 개수)"""
    files = sorted(BLACKBOX_DIR.glob("blackbox.log*"), key=lambda x: x.stat().st_mtime, reverse=True)
    total_size = sum(f.stat().st_size for f in files)
    return {
        "directory": str(BLACKBOX_DIR),
        "file_count": len(files),
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "latest_file": str(files[0]) if files else None
    }