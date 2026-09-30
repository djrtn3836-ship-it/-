# -*- coding: utf-8 -*-
"""tests/unit/test_runtime_mode.py - 안전모드 플래그 실동작 검증.

배경: `.env`의 TEST_MODE/DRY_RUN/MOCK_DATA_ENABLED/TELEGRAM_ENABLED는
오랫동안 코드에서 참조되지 않는 **죽은 플래그**였다.
`core/runtime_mode.py`가 이를 실제 동작으로 승격시켰는지 검증한다.
"""

import os

import pytest

from core.runtime_mode import RuntimeMode, load_runtime_mode, reset_runtime_mode

_FLAG_NAMES = ("TEST_MODE", "DRY_RUN", "MOCK_DATA_ENABLED", "TELEGRAM_ENABLED")


@pytest.fixture(autouse=True)
def _clean_env():
    """각 테스트 전후로 플래그 환경변수와 캐시를 초기화한다."""
    saved = {k: os.environ.get(k) for k in _FLAG_NAMES}
    for k in _FLAG_NAMES:
        os.environ.pop(k, None)
    reset_runtime_mode()
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    reset_runtime_mode()


class TestRuntimeModeDefaults:
    def test_defaults_are_production(self) -> None:
        m = load_runtime_mode(force=True)
        assert m.test_mode is False
        assert m.dry_run is False
        assert m.mock_data is False
        assert m.telegram_enabled is True
        assert m.is_safe is False
        assert m.external_io_disabled is False
        assert m.label == "PRODUCTION"

    def test_mode_is_cached_until_forced(self) -> None:
        first = load_runtime_mode(force=True)
        os.environ["TEST_MODE"] = "1"
        assert load_runtime_mode() is first  # 캐시 유지
        assert load_runtime_mode(force=True).test_mode is True


class TestFlagParsing:
    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", "Y"])
    def test_truthy_values(self, raw: str) -> None:
        os.environ["TEST_MODE"] = raw
        assert load_runtime_mode(force=True).test_mode is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", ""])
    def test_falsy_values(self, raw: str) -> None:
        os.environ["TEST_MODE"] = raw
        assert load_runtime_mode(force=True).test_mode is False


class TestSafetyComposition:
    def test_test_mode_forces_mock_and_telegram_off(self) -> None:
        os.environ["TEST_MODE"] = "1"
        os.environ["TELEGRAM_ENABLED"] = "1"  # 명시적으로 켜도 무시되어야 함
        m = load_runtime_mode(force=True)
        assert m.mock_data is True          # TEST_MODE → MOCK_DATA 자동 포함
        assert m.telegram_enabled is False  # 실발송 차단 강제
        assert m.external_io_disabled is True
        assert "TEST_MODE" in m.label and "TELEGRAM_OFF" in m.label

    def test_dry_run_disables_telegram_only(self) -> None:
        os.environ["DRY_RUN"] = "1"
        m = load_runtime_mode(force=True)
        assert m.dry_run is True
        assert m.test_mode is False
        assert m.mock_data is False
        assert m.telegram_enabled is False
        assert m.external_io_disabled is False

    def test_telegram_disabled_alone(self) -> None:
        os.environ["TELEGRAM_ENABLED"] = "0"
        m = load_runtime_mode(force=True)
        assert m.telegram_enabled is False
        assert m.is_safe is True
        assert m.label == "SAFE(TELEGRAM_OFF)"

    def test_mock_data_alone(self) -> None:
        os.environ["MOCK_DATA_ENABLED"] = "1"
        m = load_runtime_mode(force=True)
        assert m.mock_data is True
        assert m.is_safe is True
        assert "MOCK_DATA" in m.label


class TestSummary:
    def test_summary_contains_all_flags(self) -> None:
        m = RuntimeMode(test_mode=True, dry_run=True, mock_data=True, telegram_enabled=False)
        s = m.summary()
        for token in ("test_mode=True", "dry_run=True", "mock_data=True", "telegram_enabled=False"):
            assert token in s
