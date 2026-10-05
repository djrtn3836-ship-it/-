# -*- coding: utf-8 -*-
"""
core/config.py - v7.1.0 (Session 42: mypy strict 완전 적용)

v7.1.0 변경 사항:
    - 모든 메서드에 반환 타입 명시 (__new__, __init__, _get_yaml_mtime,
      _load_defaults, _load_yaml, _update_from_dict, _validate_config,
      reload_if_changed, get, get_int, get_float, get_bool, get_all)
    - _initialized를 클래스 레벨에 bool로 선언하여 "Cannot determine type" 오류 해결
      (실제 값 대입은 기존과 동일하게 __new__/__init__에서 수행 — 런타임 동작 무변경)
    - _config, type_rules 등에 정확한 제네릭 타입 인자 부여
    - ⚠️ 이 파일은 data/kiwoom_connector.py가 사용하는 core.config.get_config()의
      원본이며, app/bootstrap.py가 사용하는 config/schema.py의 get_config()와는
      별개의 모듈임에 주의(Session 42에서 확인된 사실). bootstrap.py의
      config.scheduler / config.websocket 관련 타이핑 문제는 이 파일을 고쳐도
      해결되지 않으므로, 이 파일에는 scheduler/websocket 관련 프로퍼티를
      추가하지 않음(잘못된 전제에 기반한 수정 방지).
    - 그 외 로직 100% 무변경 (int→float 자동 변환 등 기존 동작 완전 보존)
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from dotenv import load_dotenv

from core.logger import setup_logger

logger = setup_logger("config")


class ConfigError(Exception):
    pass


class ConfigManager:
    _instance: Optional["ConfigManager"] = None
    _last_mtime: float = 0.0
    _initialized: bool

    def __new__(cls) -> "ConfigManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._config: Dict[str, Any] = {}
        self._config_dir = Path(__file__).parent.parent / "config"
        self._env_loaded: bool = False

        load_dotenv()
        self._env_loaded = True
        self._load_defaults()
        self._load_yaml()
        self._resolve_nested_aliases()
        self._validate_config()
        self._last_mtime = self._get_yaml_mtime()

    # YAML은 섹션형(market.max_subscriptions)으로 쓰이지만 DEFAULTS는 평탄형(max_subscriptions)이다.
    # _update_from_dict가 "market_max_subscriptions"로 저장하므로 YAML 값이 영원히 반영되지 않던
    # 버그(P12-2: 실제 core=500 / schema=200 / monitor=195 하드코딩)를 여기서 해소한다.
    _NESTED_ALIASES: Dict[str, str] = {
        "max_subscriptions": "market_max_subscriptions",
        "price_change_ratio": "market_price_change_ratio",
        "cooldown_seconds": "market_cooldown_seconds",
        "emergency_threshold": "market_emergency_threshold",
    }

    def _resolve_nested_aliases(self) -> None:
        """섹션형 YAML 키를 평탄형 표준 키로 승격한다(config.yaml = 단일 소스)."""
        for canonical, nested in self._NESTED_ALIASES.items():
            if nested not in self._config:
                continue
            value = self._config[nested]
            if self._config.get(canonical) != value:
                logger.info(f"⚙️ 설정 단일화: {canonical} = {value} (← {nested})")
            self._config[canonical] = value

    def _get_yaml_mtime(self) -> float:
        config_file = self._config_dir / "config.yaml"
        if config_file.exists():
            return config_file.stat().st_mtime
        return 0.0

    def _load_defaults(self) -> None:
        self._config = {
            "ws_url": "wss://api.kiwoom.com:10000/api/dostk/websocket",
            "ws_mock_url": "wss://mockapi.kiwoom.com:10000/api/dostk/websocket",
            "ws_ping_interval": 20,
            "ws_ping_timeout": 60,
            "ws_close_timeout": 10,
            "ws_login_timeout": 10,
            "ws_reg_timeout": 5,
            "ws_silence_timeout": 60,
            "rate_limit_capacity": 5,
            "rate_limit_refill": 5.0,
            "price_change_ratio": 0.02,
            "cooldown_seconds": 300,
            "emergency_threshold": 0.05,
            "max_subscriptions": 500,
            "daily_report_hour": 7,
            "daily_report_minute": 0,
            "feedback_hour": 17,
            "feedback_minute": 0,
            "weekly_pdf_day": "mon",
            "weekly_pdf_hour": 6,
            "weekly_pdf_minute": 0,
            "ohlcv_hour": 16,
            "ohlcv_minute": 30,
            "reconnect_max_attempts": 5,
            "reconnect_base_delay": 2,
            "reconnect_max_delay": 60,
            "reconnect_interval": 30,
            "queue_maxsize": 100000,
            "connect_retry_interval": 60,
            "log_level": "DEBUG",
            "structured_logging": False,
            "trading_max_hold_hours": 2.0,
            "trading_trail_aggressive_threshold": 5.0,
            "trading_atr_multiplier_stop": 2.0,
            "trading_atr_multiplier_trail": 1.5,
            "trading_atr_spike_threshold": 0.3,
            "trading_momentum_weight": 0.08,
            "trading_ml_weight": 0.18,
            "trading_sentiment_weight": 0.02,
            "trading_base_weight": 0.42,
            "trading_strategy_weight": 0.30,
            "trading_fill_ratio_reject": 0.30,
            "trading_fill_ratio_reduce": 0.70,
            "trading_order_volume_ratio": 0.008,
            "trading_order_volume_min": 10,
            "trading_order_volume_max": 500,
            "strategy_default_trend_weight": 0.40,
            "strategy_default_reversal_weight": 0.30,
            "strategy_default_breakout_weight": 0.30,
            "risk_var_confidence": 0.95,
            "risk_var_num_simulations": 10000,
            "risk_var_lookback_days": 252,
            "risk_var_update_interval": 300,
        }

    def _load_yaml(self) -> None:
        config_file = self._config_dir / "config.yaml"
        if config_file.exists():
            try:
                with open(config_file, encoding="utf-8") as f:
                    yaml_config = yaml.safe_load(f)
                    if yaml_config:
                        self._update_from_dict(yaml_config)
                        logger.info(f"✅ YAML 설정 로드 완료: {config_file}")
            except Exception as e:
                logger.warning(f"⚠️ YAML 설정 로드 실패: {e}")

    def _update_from_dict(self, data: Dict[str, Any], prefix: str = "") -> None:
        for key, value in data.items():
            full_key = f"{prefix}{key}" if prefix else key
            if isinstance(value, dict):
                self._update_from_dict(value, f"{full_key}_")
            else:
                self._config[full_key] = value

    def _validate_config(self) -> None:
        errors: List[str] = []
        warnings: List[str] = []

        type_rules: Dict[str, Tuple[type, float, float]] = {
            "ws_ping_interval": (int, 5, 120),
            "ws_ping_timeout": (int, 10, 300),
            "ws_close_timeout": (int, 5, 60),
            "rate_limit_capacity": (int, 1, 20),
            "price_change_ratio": (float, 0.005, 0.1),
            "cooldown_seconds": (int, 60, 3600),
            "emergency_threshold": (float, 0.01, 0.2),
            "max_subscriptions": (int, 10, 1000),
            "queue_maxsize": (int, 1000, 1000000),
            "reconnect_max_attempts": (int, 1, 20),
            "reconnect_base_delay": (int, 1, 10),
            "reconnect_max_delay": (int, 10, 300),
            "connect_retry_interval": (int, 10, 600),
            "trading_max_hold_hours": (float, 0.5, 24.0),
            "trading_atr_multiplier_stop": (float, 0.5, 5.0),
            "trading_atr_multiplier_trail": (float, 0.5, 5.0),
            "trading_momentum_weight": (float, 0.0, 0.5),
            "trading_ml_weight": (float, 0.0, 0.5),
            "trading_base_weight": (float, 0.0, 1.0),
            "trading_strategy_weight": (float, 0.0, 1.0),
            "trading_fill_ratio_reject": (float, 0.05, 0.50),
            "trading_fill_ratio_reduce": (float, 0.30, 0.95),
            "trading_order_volume_ratio": (float, 0.001, 0.05),
            "trading_order_volume_min": (int, 1, 100),
            "trading_order_volume_max": (int, 100, 10000),
            "risk_var_confidence": (float, 0.80, 0.99),
            "risk_var_num_simulations": (int, 1000, 100000),
            "risk_var_lookback_days": (int, 30, 1000),
            "risk_var_update_interval": (int, 30, 3600),
            "strategy_default_trend_weight": (float, 0.0, 1.0),
            "strategy_default_reversal_weight": (float, 0.0, 1.0),
            "strategy_default_breakout_weight": (float, 0.0, 1.0),
        }

        for key, (exp_type, min_val, max_val) in type_rules.items():
            value = self._config.get(key)
            if value is None:
                warnings.append(f"⚠️ 설정 '{key}' 없음 → 기본값 사용")
                continue

            # 🔥 P1-6: int → float 자동 변환
            if exp_type == float and isinstance(value, int):
                self._config[key] = float(value)
                value = self._config[key]

            if not isinstance(value, exp_type):
                errors.append(f"❌ 설정 '{key}' 타입 오류: {type(value).__name__} (필요: {exp_type.__name__})")
                continue

            if isinstance(value, (int, float)):
                if value < min_val or value > max_val:
                    errors.append(f"❌ 설정 '{key}' 범위 초과: {value} (허용: {min_val}~{max_val})")

        if self._config.get("weekly_pdf_day") not in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]:
            warnings.append(f"⚠️ weekly_pdf_day='{self._config.get('weekly_pdf_day')}' → 'mon'으로 대체")
            self._config["weekly_pdf_day"] = "mon"

        log_level = str(self._config.get("log_level", "DEBUG")).upper()
        if log_level not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            warnings.append(f"⚠️ log_level='{log_level}' → 'DEBUG'로 대체")
            self._config["log_level"] = "DEBUG"

        if errors:
            for err in errors:
                logger.error(err)
            raise ConfigError(f"설정 검증 실패: {len(errors)}개 오류")

        for warn in warnings:
            logger.warning(warn)

    def reload_if_changed(self) -> bool:
        current_mtime = self._get_yaml_mtime()
        if current_mtime > self._last_mtime:
            logger.info("🔄 config.yaml 변경 감지 → 설정 재로드")
            self._load_yaml()
            self._validate_config()
            self._last_mtime = current_mtime
            return True
        return False

    def get(self, key: str, default: Any = None) -> Any:
        env_key = key.upper()
        env_value = os.getenv(env_key)
        if env_value is not None:
            original = self._config.get(key)
            if isinstance(original, bool):
                return env_value.lower() in ("true", "yes", "1", "on")
            elif isinstance(original, int):
                try:
                    return int(env_value)
                except (ValueError, TypeError):
                    return original
            elif isinstance(original, float):
                try:
                    return float(env_value)
                except (ValueError, TypeError):
                    return original
            return env_value
        return self._config.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        value = self.get(key, default)
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        value = self.get(key, default)
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "yes", "1", "on")
        return bool(value)

    def get_all(self) -> Dict[str, Any]:
        return self._config.copy()


_config_manager: Optional[ConfigManager] = None


def get_config() -> ConfigManager:
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def reload_config() -> ConfigManager:
    global _config_manager
    _config_manager = None
    return get_config()
