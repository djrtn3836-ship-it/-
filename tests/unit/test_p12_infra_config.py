# -*- coding: utf-8 -*-
"""tests/unit/test_p12_infra_config.py - P12-1(shim) / P12-2(설정 단일화) 검증."""

from pathlib import Path

import pytest


class TestKiwoomInfraShim:
    """P12-1: infrastructure/kiwoom 정식 경로 실체화.

    배경: 폴더가 없어 app/bootstrap.py의 try가 항상 실패 →
          data/, scanner/ 모듈이 조용히 활성 경로로 동작(문서↔실제 불일치).
    """

    def test_package_exists(self) -> None:
        import infrastructure.kiwoom as pkg

        assert Path(pkg.__file__).name == "__init__.py"

    def test_connector_is_same_object(self) -> None:
        """shim은 원본과 '같은 클래스'여야 한다(복제 시 타입 불일치 사고)."""
        from data.kiwoom_connector import KiwoomConnectorV512 as Original
        from infrastructure.kiwoom import KiwoomConnectorV512

        assert KiwoomConnectorV512 is Original

    def test_monitor_is_same_object(self) -> None:
        from infrastructure.kiwoom.monitor import RealtimeMonitor
        from scanner.realtime_monitor import RealtimeMonitor as Original

        assert RealtimeMonitor is Original

    def test_bootstrap_import_path_resolves_without_fallback(self) -> None:
        """bootstrap의 try가 실제로 성공해야 한다(폴백 미사용)."""
        try:
            from infrastructure.kiwoom import KiwoomConnectorV512  # noqa: F401
            from infrastructure.kiwoom.monitor import RealtimeMonitor  # noqa: F401
        except ImportError as e:                      # pragma: no cover
            pytest.fail(f"infrastructure.kiwoom 경로가 여전히 실패: {e}")

    def test_no_class_definitions_in_shim(self) -> None:
        """shim에 클래스 정의가 생기면(복제) 위험 → 재수출만 허용."""
        import inspect

        import infrastructure.kiwoom as pkg

        src = inspect.getsource(pkg)
        assert "class " not in src.replace("재수출", "")


class TestConfigUnification:
    """P12-2: config.yaml이 단일 소스 (core.config / config.schema 동일 값)."""

    def test_both_config_systems_agree(self) -> None:
        from config.schema import get_config as schema_get
        from core.config import get_config as core_get

        core, schema = core_get(), schema_get()

        assert core.get_int("max_subscriptions") == schema.max_subscriptions

    def test_market_keys_come_from_yaml(self) -> None:
        """YAML(market 섹션)의 값이 실제로 반영되는지 — 과거엔 죽은 값이었다."""
        import yaml

        from config.schema import get_config as schema_get
        from core.config import get_config as core_get

        yaml_path = Path(__file__).parent.parent.parent / "config" / "config.yaml"
        market = (yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}).get("market", {})

        core, schema = core_get(), schema_get()
        for key in ("max_subscriptions", "price_change_ratio", "cooldown_seconds", "emergency_threshold"):
            if key not in market:
                continue
            expected = market[key]
            assert core.get(key) == expected, f"core.config {key} 불일치"
            assert getattr(schema, key) == expected, f"config.schema {key} 불일치"

    def test_subscription_limit_within_kiwoom_cap(self) -> None:
        """키움 실시간 구독 한도(200) 이내인지 — 500 같은 값이면 연결이 깨진다."""
        from config.schema import get_config as schema_get
        from core.config import get_config as core_get

        for value in (core_get().get_int("max_subscriptions"),
                      schema_get().max_subscriptions):
            assert 10 <= value <= 200, f"구독 한도 비정상: {value}"

    def test_nested_alias_resolution_present(self) -> None:
        """중첩 별칭 해석 로직이 실제로 존재하는지(회귀 방지)."""
        from core.config import ConfigManager

        assert "market_max_subscriptions" in ConfigManager._NESTED_ALIASES.values()
        assert "max_subscriptions" in ConfigManager._NESTED_ALIASES

    def test_monitor_uses_config_not_hardcode(self) -> None:
        """realtime_monitor가 195 하드코딩이 아니라 설정을 참조해야 한다."""
        src = (Path(__file__).parent.parent.parent / "scanner" / "realtime_monitor.py").read_text(
            encoding="utf-8"
        )
        assert 'get_int("max_subscriptions"' in src
