# -*- coding: utf-8 -*-
"""
core/logger.py - v7.1.3 (Session 32: get_logger_status 중첩 dict 타입 오류 해결)

v7.1.2 -> v7.1.3 변경 사항:
    - get_logger_status(): "loggers" 딕셔너리 컴프리헨션을 반환문에 직접 인라인하지
      않고, dict[str, Any]로 명시된 중간 변수(loggers_info)에 먼저 할당.
      mypy가 반환 타입 애노테이션을 인라인 중첩 컴프리헨션까지 양방향으로
      전파시키지 못해 발생하던 [type-arg] 오류(line 240) 해결.
    - 나머지 코드는 v7.1.2와 100% 동일 (로직/동작 무변경)
"""

import gzip
import json
import logging
import os
import shutil
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
STRUCTURED_LOGGING = os.getenv("STRUCTURED_LOGGING", "true").lower() in ("true", "1", "yes", "on")
LOG_DIR = os.getenv("LOG_DIR", "./logs")
LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "10485760"))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "10"))


class Colors:
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    @staticmethod
    def level_color(level: str) -> str:
        return {
            "DEBUG": Colors.CYAN,
            "INFO": Colors.GREEN,
            "WARNING": Colors.YELLOW,
            "ERROR": Colors.RED,
            "CRITICAL": Colors.MAGENTA + Colors.BOLD,
        }.get(level, Colors.WHITE)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        dt = datetime.fromtimestamp(record.created)
        timestamp = dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z"

        log_entry: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
            "process": record.process,
            "thread": record.threadName,
        }

        if record.exc_info:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exc() if record.exc_info[2] else None,
            }

        if hasattr(record, "extra") and isinstance(record.extra, dict):
            log_entry.update(record.extra)

        return json.dumps(log_entry, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        level_color = Colors.level_color(record.levelname)
        reset = Colors.RESET
        msg = record.getMessage()
        if record.exc_info:
            msg += f"\n{traceback.format_exc()}"
        logger_name = f"[{record.name}]" if record.name != "root" else ""
        ts = self.formatTime(record, "%H:%M:%S")
        extra_info = ""
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            extra_parts = [f"{k}={v}" for k, v in record.extra.items()]
            if extra_parts:
                extra_info = f" ({', '.join(extra_parts)})"
        return (
            f"{Colors.DIM}{ts}{Colors.RESET} "
            f"{level_color}{record.levelname:<8}{reset} "
            f"{logger_name}{record.funcName}:{record.lineno}{extra_info} - {msg}"
        )


class GzipRotatingFileHandler(RotatingFileHandler):
    def __init__(
        self,
        filename: str | Path,
        mode: str = "a",
        maxBytes: int = 0,
        backupCount: int = 0,
        encoding: str | None = None,
        delay: bool = False,
    ) -> None:
        super().__init__(filename, mode, maxBytes, backupCount, encoding, delay)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gzip_compressor")

    def doRollover(self) -> None:
        super().doRollover()
        for i in range(self.backupCount, 0, -1):
            src = Path(self.baseFilename).with_suffix(f".log.{i}")
            if src.exists() and src.suffix != ".gz":
                dst = src.with_suffix(".log.gz")
                self._executor.submit(self._compress_file, src, dst)

    def _compress_file(self, src: Path, dst: Path) -> None:
        try:
            with open(src, "rb") as f_in:
                with gzip.open(dst, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            src.unlink()
        except Exception:
            pass

    def close(self) -> None:
        if hasattr(self, "_executor"):
            self._executor.shutdown(wait=False)
        super().close()


def setup_logger(
    name: str = "system",
    log_dir: str | None = None,
    level: str | None = None,
    structured: bool | None = None,
    use_gzip: bool = True,
    console_output: bool = True,
) -> logging.Logger:
    resolved_log_dir: str = log_dir or LOG_DIR
    resolved_level: str = level or LOG_LEVEL
    resolved_structured: bool = structured if structured is not None else STRUCTURED_LOGGING

    log_path = Path(resolved_log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, resolved_level.upper(), logging.DEBUG))

    if logger.handlers:
        return logger

    handler_class = GzipRotatingFileHandler if use_gzip else RotatingFileHandler
    file_handler = handler_class(
        log_path / f"{name}.log", maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)

    file_formatter: logging.Formatter
    if resolved_structured:
        file_formatter = JsonFormatter()
    else:
        file_formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)-8s [%(name)s:%(lineno)d] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, resolved_level.upper(), logging.DEBUG))
        console_handler.setFormatter(ConsoleFormatter())
        logger.addHandler(console_handler)

    return logger


def set_log_level(logger_name: str, level: str) -> bool:
    try:
        logger = logging.getLogger(logger_name)
        new_level = getattr(logging, level.upper(), logging.DEBUG)
        logger.setLevel(new_level)
        for handler in logger.handlers:
            handler.setLevel(new_level)
        logger.info(f"🔧 로그 레벨 변경: {logger_name} → {level.upper()}")
        return True
    except Exception as e:
        print(f"❌ 로그 레벨 변경 실패: {e}")
        return False


def set_global_log_level(level: str) -> bool:
    try:
        new_level = getattr(logging, level.upper(), logging.DEBUG)
        logging.root.setLevel(new_level)
        for handler in logging.root.handlers:
            handler.setLevel(new_level)
        print(f"🔧 글로벌 로그 레벨 변경: {level.upper()}")
        return True
    except Exception as e:
        print(f"❌ 글로벌 로그 레벨 변경 실패: {e}")
        return False


def log_exception(logger: logging.Logger, msg: str, exc: Exception, extra: dict[str, Any] | None = None) -> None:
    if extra is None:
        extra = {}
    extra["exception_type"] = type(exc).__name__
    extra["exception_msg"] = str(exc)
    logger.error(msg, exc_info=exc, extra={"extra": extra})


def get_logger_status() -> dict[str, Any]:
    # 🔧 Session 32 수정: 중첩 컴프리헨션을 중간 변수로 분리하여 [type-arg] 해결
    loggers_info: dict[str, Any] = {
        name: {
            "level": logging.getLevelName(lg.level),
            "handlers": len(lg.handlers),
        }
        for name, lg in logging.root.manager.loggerDict.items()
        if isinstance(lg, logging.Logger)
    }
    return {
        "root_level": logging.getLevelName(logging.root.level),
        "handlers": [str(h) for h in logging.root.handlers],
        "loggers": loggers_info,
    }


system_logger = setup_logger("system")
