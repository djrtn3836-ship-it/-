"""
Logging System v5.1.2
파일 + 콘솔 로깅, 로그 로테이션 지원
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from datetime import datetime


def setup_logger(name: str = "system", log_dir: str = "./logs") -> logging.Logger:
    """로거 설정 (파일 + 콘솔)"""
    
    # 로그 디렉토리 생성
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    # 로거 생성
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # 이미 핸들러가 있으면 추가하지 않음
    if logger.handlers:
        return logger
    
    # 포맷 설정
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # 콘솔 핸들러
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # 파일 핸들러 (Rotating)
    file_handler = RotatingFileHandler(
        log_path / f"{name}.log",
        maxBytes=10_485_760,  # 10MB
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Shadow 로그 별도 파일
    if name == "shadow":
        shadow_handler = RotatingFileHandler(
            log_path / "shadow.log",
            maxBytes=10_485_760,
            backupCount=10,
            encoding="utf-8"
        )
        shadow_handler.setLevel(logging.DEBUG)
        shadow_handler.setFormatter(formatter)
        logger.addHandler(shadow_handler)
    
    return logger


# 기본 로거 인스턴스
system_logger = setup_logger("system")