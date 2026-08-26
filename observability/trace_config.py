# -*- coding: utf-8 -*-
"""
observability/trace_config.py - V10 Unified debug tracing config manager
- Global ON/OFF and per-module ON/OFF
- Hot-reload support with 1-second file watch on config/trace_config.json
- Real-time debug mode switching without redeployment
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

TRACE_CONFIG_PATH = Path(__file__).parent.parent / "config" / "trace_config.json"


@dataclass
class TraceConfig:
    """Tracing config data class"""
    global_enabled: bool = True
    module_overrides: dict[str, bool] = field(default_factory=dict)


class TraceConfigManager:
    """Tracing config manager (singleton) with Hot-reload"""

    _instance: Optional["TraceConfigManager"] = None

    def __new__(cls) -> "TraceConfigManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        self._config: TraceConfig = TraceConfig()
        self._last_mtime: float = 0.0
        self._last_check: float = 0.0
        self._reload_interval: float = 1.0
        self._load()

    def _load(self) -> None:
        if not TRACE_CONFIG_PATH.exists():
            self._save()
            return
        try:
            raw_data = TRACE_CONFIG_PATH.read_text(encoding="utf-8")
            data = json.loads(raw_data)
            self._config = TraceConfig(**data)
            self._last_mtime = TRACE_CONFIG_PATH.stat().st_mtime
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: TraceConfig file parsing failed: {e}")

    def _save(self) -> None:
        try:
            TRACE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            TRACE_CONFIG_PATH.write_text(
                json.dumps(
                    {
                        "global_enabled": self._config.global_enabled,
                        "module_overrides": self._config.module_overrides,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self._last_mtime = TRACE_CONFIG_PATH.stat().st_mtime
        except OSError as e:
            print(f"Warning: TraceConfig file save failed: {e}")

    def _maybe_reload(self) -> None:
        now = time.monotonic()
        if now - self._last_check < self._reload_interval:
            return
        self._last_check = now
        if not TRACE_CONFIG_PATH.exists():
            return
        try:
            current_mtime = TRACE_CONFIG_PATH.stat().st_mtime
            if current_mtime > self._last_mtime:
                self._load()
        except OSError:
            pass

    def is_enabled(self, module_name: str) -> bool:
        self._maybe_reload()
        if module_name in self._config.module_overrides:
            return self._config.module_overrides[module_name]
        return self._config.global_enabled

    def set_global(self, enabled: bool) -> None:
        self._config.global_enabled = enabled
        self._config.module_overrides.clear()
        self._save()

    def set_module(self, module_name: str, enabled: bool) -> None:
        self._config.module_overrides[module_name] = enabled
        self._save()

    def get_status(self) -> dict:
        self._maybe_reload()
        return {
            "global_enabled": self._config.global_enabled,
            "overrides": self._config.module_overrides.copy(),
        }


_config_manager = TraceConfigManager()


def get_trace_manager() -> TraceConfigManager:
    return _config_manager