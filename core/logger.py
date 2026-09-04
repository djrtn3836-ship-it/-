# -*- coding: utf-8 -*-
"""
core/logger.py - v7.1.2 (Session 30: mypy strict 적용)

v7.1.1 -> v7.1.2 변경 사항 (실제 mypy 오류 10건을 줄 번호 기준으로 정밀 대조하여 수정):
    - GzipRotatingFileHandler.__init__: 파라미터 전체 타입 명시
      (filename: str | Path — 부모 RotatingFileHandler가 PathLike도 허용하므로
       str만으로 좁히면 리스코프 치환 원칙 위반으로 strict에서 새 오류가 발생할
       위험이 있어 str | Path로 지정. 이렇게 하면 호출부(setup_logger)가 Path
       객체를 그대로 전달하는 기존 동작을 전혀 바꿀 필요가 없음)
    - doRollover/_compress_file/close: 반환 타입 -> None 명시
    - setup_logger: log_dir/level/structured 파라미터를 X | None으로 수정
      (기본값이 None인데 타입은 str/bool로 선언되어 있던 PEP 484 위반 [assignment] 해결)
    - setup_logger 내부: file_formatter를 if/else 분기 전에 logging.Formatter로
      명시 선언 (JsonFormatter로 좁혀진 뒤 부모 Formatter로 재할당하며 발생하던
      [assignment] 오류 해결. else 분기는 실제로 정상 동작하는 경로였으므로
      런타임 버그는 아니었음)
    - get_logger_status: 반환 타입 dict[str, Any] 명시
    - log_exception: 반환 타입 -> None 명시 (일관성을 위한 안전한 보강)
    - Colors/JsonFormatter/ConsoleFormatter는 mypy 오류 목록에 없어 원본 그대로 보존
      (최소 침습 수정 원칙 유지)
    - 로직/동작 100% 무변경 — 타입 힌트만 추가
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

        log_entry = {
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


# ============================================================
# 🔥 P1-8: GzipRotatingFileHandler (비동기 압축)
# ============================================================
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
        # 🔥 P1-8: 압축 전용 스레드 풀 (최대 1개)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gzip_compressor")

    def doRollover(self) -> None:
        super().doRollover()
        # 백업 파일을 gzip으로 압축 (스레드 풀에서 비동기 실행)
        for i in range(self.backupCount, 0, -1):
            src = Path(self.baseFilename).with_suffix(f".log.{i}")
            if src.exists() and src.suffix != ".gz":
                dst = src.with_suffix(".log.gz")
                # 스레드 풀에 압축 작업 제출 (메인 루프 블로킹 없음)
                self._executor.submit(self._compress_file, src, dst)

    def _compress_file(self, src: Path, dst: Path) -> None:
        """실제 압축 작업 (별도 스레드에서 실행)"""
        try:
            with open(src, "rb") as f_in:
                with gzip.open(dst, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            src.unlink()
        except Exception:
            # 로깅 시스템 내부에서 예외가 발생하면 무시 (로거 자체가 죽지 않도록)
            pass

    def close(self) -> None:
        """종료 시 스레드 풀 정리"""
        if hasattr(self, "_executor"):
            self._executor.shutdown(wait=False)
        super().close()


# ============================================================
# 로거 설정 함수
# ============================================================
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


def log_exception(logger: logging.Logger, msg: str, exc: Exception, extra: dict | None = None) -> None:
    if extra is None:
        extra = {}
    extra["exception_type"] = type(exc).__name__
    extra["exception_msg"] = str(exc)
    logger.error(msg, exc_info=exc, extra={"extra": extra})


def get_logger_status() -> dict[str, Any]:
    return {
        "root_level": logging.getLevelName(logging.root.level),
        "handlers": [str(h) for h in logging.root.handlers],
        "loggers": {
            name: {
                "level": logging.getLevelName(logger.level),
                "handlers": len(logger.handlers),
            }
            for name, logger in logging.root.manager.loggerDict.items()
            if isinstance(logger, logging.Logger)
        },
    }


system_logger = setup_logger("system")
