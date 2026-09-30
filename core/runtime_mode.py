# -*- coding: utf-8 -*-
"""core/runtime_mode.py - 실행 모드(안전모드) 단일 소스.

배경:
    `.env`에 `TEST_MODE` / `DRY_RUN` / `MOCK_DATA_ENABLED` / `TELEGRAM_ENABLED` /
    `DB_TYPE` 플래그가 있었으나 **코드에서 0건 참조되는 죽은 플래그**였다.
    이 모듈이 그 플래그들을 실제 동작으로 승격시킨다.

의미:
    TEST_MODE          : 1이면 외부 연결(키움/거시/뉴스/DART) 없이 부팅
    DRY_RUN            : 1이면 부작용(텔레그램 실발송 등) 차단 — 로그로만 대체
    MOCK_DATA_ENABLED  : 1이면 모의 데이터 사용 (TEST_MODE면 자동 포함)
    TELEGRAM_ENABLED   : 0이면 텔레그램 전송/폴링 비활성

우선순위(오설정 사고 방지):
    TEST_MODE=1  → mock_data=True, telegram_enabled=False 강제
    DRY_RUN=1    → telegram_enabled=False 강제
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUE_VALUES


@dataclass(frozen=True)
class RuntimeMode:
    """현재 프로세스의 실행 모드."""

    test_mode: bool = False
    dry_run: bool = False
    mock_data: bool = False
    telegram_enabled: bool = True

    @property
    def is_safe(self) -> bool:
        """안전모드(부작용/외부연결 제한) 여부."""
        return self.test_mode or self.dry_run or self.mock_data or not self.telegram_enabled

    @property
    def external_io_disabled(self) -> bool:
        """외부 네트워크 연결(키움/거시/뉴스/DART)을 건너뛰어야 하는지."""
        return self.test_mode

    @property
    def label(self) -> str:
        parts = []
        if self.test_mode:
            parts.append("TEST_MODE")
        if self.dry_run:
            parts.append("DRY_RUN")
        if self.mock_data:
            parts.append("MOCK_DATA")
        if not self.telegram_enabled:
            parts.append("TELEGRAM_OFF")
        return "SAFE(" + "+".join(parts) + ")" if parts else "PRODUCTION"

    def summary(self) -> str:
        return (
            f"mode={self.label} test_mode={self.test_mode} dry_run={self.dry_run} "
            f"mock_data={self.mock_data} telegram_enabled={self.telegram_enabled}"
        )


_cached: RuntimeMode | None = None


def load_runtime_mode(force: bool = False) -> RuntimeMode:
    """환경변수에서 실행 모드를 읽어 캐시한다."""
    global _cached
    if _cached is not None and not force:
        return _cached

    test_mode = _env_bool("TEST_MODE", False)
    dry_run = _env_bool("DRY_RUN", False)
    mock_data = _env_bool("MOCK_DATA_ENABLED", False) or test_mode
    telegram_enabled = _env_bool("TELEGRAM_ENABLED", True) and not (test_mode or dry_run)

    _cached = RuntimeMode(
        test_mode=test_mode,
        dry_run=dry_run,
        mock_data=mock_data,
        telegram_enabled=telegram_enabled,
    )
    return _cached


def get_runtime_mode() -> RuntimeMode:
    """캐시된 실행 모드(없으면 로드)."""
    return load_runtime_mode(force=False)


def reset_runtime_mode() -> None:
    """캐시 초기화(테스트용)."""
    global _cached
    _cached = None
