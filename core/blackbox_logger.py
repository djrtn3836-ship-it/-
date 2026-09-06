# -*- coding: utf-8 -*-
"""
core/blackbox_logger.py - v1.1.0 (Session 35: mypy strict 적용, 로직 무변경)
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

BLACKBOX_DIR = Path(__file__).parent.parent / "logs" / "blackbox"
BLACKBOX_DIR.mkdir(parents=True, exist_ok=True)

blackbox_logger = logging.getLogger("BLACKBOX")
blackbox_logger.setLevel(logging.DEBUG)

log_file_path = BLACKBOX_DIR / "blackbox.log"
file_handler = RotatingFileHandler(
    log_file_path,
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)

formatter = logging.Formatter(
    "[%(asctime)s] [%(levelname)-8s] [%(filename)s:%(lineno)d] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(formatter)

if not blackbox_logger.handlers:
    blackbox_logger.addHandler(file_handler)


def log_raw_data(data: str, source: str = "WEBSOCKET") -> None:
    blackbox_logger.debug(f"[RAW][{source}] {data}")


def log_event(event: str, details: Optional[Dict[str, Any]] = None) -> None:
    if details:
        blackbox_logger.info(f"[EVENT] {event} | {details}")
    else:
        blackbox_logger.info(f"[EVENT] {event}")


def log_error(error_msg: str, error_obj: Optional[Exception] = None) -> None:
    if error_obj:
        blackbox_logger.error(f"[ERROR] {error_msg} | Exception: {type(error_obj).__name__} - {error_obj!s}")
    else:
        blackbox_logger.error(f"[ERROR] {error_msg}")


def log_performance(module: str, action: str, elapsed_ms: float) -> None:
    blackbox_logger.debug(f"[PERF] {module} | {action} | {elapsed_ms:.2f}ms")


def get_status() -> Dict[str, Any]:
    files = sorted(BLACKBOX_DIR.glob("blackbox.log*"), key=lambda x: x.stat().st_mtime, reverse=True)
    total_size = sum(f.stat().st_size for f in files)
    return {
        "directory": str(BLACKBOX_DIR),
        "file_count": len(files),
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "latest_file": str(files[0]) if files else None,
    }
