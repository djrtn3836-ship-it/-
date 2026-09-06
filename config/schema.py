# -*- coding: utf-8 -*-
"""
config/schema.py - V10 통합 설정 스키마 v1.2.2 (Session 35: 잔여 구문 오류 완전 제거)

pydantic.mypy 플러그인이 정상 동작하므로 type: ignore 주석이 전혀 필요 없습니다.
로직/동작 100% 무변경, 타입 힌트만 유지.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import yaml


class TradingConfig(BaseModel):
    """트레이딩 관련 설정"""
    max_hold_hours: float = Field(2.0, ge=0.5, le=24.0)
    trail_aggressive_threshold: float = Field(5.0, ge=0.0, le=20.0)
    atr_multiplier_stop: float = Field(2.0, ge=0.5, le=5.0)
    atr_multiplier_trail: float = Field(1.5, ge=0.5, le=5.0)
    atr_spike_threshold: float = Field(0.3, ge=0.1, le=1.0)
    atr_fallback_ratio: float = Field(0.01, ge=0.001, le=0.05)
    fill_ratio_reject: float = Field(0.30, ge=0.05, le=0.50)
    fill_ratio_reduce: float = Field(0.70, ge=0.30, le=0.95)
    order_volume_ratio: float = Field(0.008, ge=0.001, le=0.05)
    order_volume_min: int = Field(10, ge=1, le=100)
    order_volume_max: int = Field(500, ge=100, le=10000)
    momentum_weight: float = Field(0.08, ge=0.0, le=0.5)
    ml_weight: float = Field(0.18, ge=0.0, le=0.5)
    sentiment_weight: float = Field(0.02, ge=0.0, le=0.5)
    base_weight: float = Field(0.42, ge=0.0, le=1.0)
    strategy_weight: float = Field(0.30, ge=0.0, le=1.0)


class RiskConfig(BaseModel):
    """리스크 관리 설정"""
    var_confidence: float = Field(0.95, ge=0.80, le=0.99)
    var_num_simulations: int = Field(10000, ge=1000, le=100000)
    var_lookback_days: int = Field(252, ge=30, le=1000)
    var_update_interval: int = Field(300, ge=30, le=3600)


class SchedulerConfig(BaseModel):
    """스케줄러 설정"""
    daily_report_hour: int = Field(7, ge=0, le=23)
    daily_report_minute: int = Field(0, ge=0, le=59)
    feedback_hour: int = Field(17, ge=0, le=23)
    feedback_minute: int = Field(0, ge=0, le=59)
    weekly_pdf_day: str = Field("mon", pattern="^(mon|tue|wed|thu|fri|sat|sun)$")
    weekly_pdf_hour: int = Field(6, ge=0, le=23)
    weekly_pdf_minute: int = Field(0, ge=0, le=59)
    ohlcv_hour: int = Field(16, ge=0, le=23)
    ohlcv_minute: int = Field(30, ge=0, le=59)
    macro_update_hour: int = Field(8, ge=0, le=23)
    macro_update_minute: int = Field(0, ge=0, le=59)


class WebSocketConfig(BaseModel):
    """WebSocket 설정"""
    ws_url: str = "wss://api.kiwoom.com:10000/api/dostk/websocket"
    ping_interval: int = Field(20, ge=5, le=120)
    ping_timeout: int = Field(60, ge=10, le=300)
    close_timeout: int = Field(10, ge=5, le=60)
    silence_timeout: int = Field(60, ge=10, le=300)
    reconnect_max_attempts: int = Field(5, ge=1, le=20)
    reconnect_base_delay: int = Field(2, ge=1, le=10)
    reconnect_max_delay: int = Field(60, ge=10, le=300)
    connect_retry_interval: int = Field(60, ge=10, le=600)


class AppConfig(BaseModel):
    """애플리케이션 전체 설정"""
    log_level: str = Field("INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    queue_maxsize: int = Field(100000, ge=1000, le=1000000)
    max_subscriptions: int = Field(200, ge=10, le=1000)
    cooldown_seconds: int = Field(300, ge=60, le=3600)
    emergency_threshold: float = Field(0.05, ge=0.01, le=0.2)
    price_change_ratio: float = Field(0.02, ge=0.005, le=0.1)
    rate_limit_capacity: int = Field(5, ge=1, le=20)
    rate_limit_refill: float = Field(5.0, ge=1.0, le=10.0)

    trading: TradingConfig = Field(default=TradingConfig())
    risk: RiskConfig = Field(default=RiskConfig())
    scheduler: SchedulerConfig = Field(default=SchedulerConfig())
    websocket: WebSocketConfig = Field(default=WebSocketConfig())


class ConfigManager:
    """설정 관리자 (Singleton)"""
    _instance: Optional["ConfigManager"] = None
    _config: Optional[AppConfig] = None

    def __new__(cls) -> "ConfigManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._config is not None:
            return
        load_dotenv(override=True)
        self._load_config()

    def _load_config(self) -> None:
        """YAML + .env 통합 로드"""
        yaml_path = Path(__file__).parent / "config.yaml"
        yaml_data: Dict[str, Any] = {}
        if yaml_path.exists():
            try:
                with open(yaml_path, encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                    if loaded:
                        yaml_data = dict(loaded)
            except Exception as e:
                print(f"YAML 로드 실패: {e}")

        env_overrides: Dict[str, Any] = {}
        for key in yaml_data.keys():
            env_key = key.upper()
            env_value = os.getenv(env_key)
            if env_value is not None:
                if isinstance(yaml_data[key], bool):
                    env_overrides[key] = env_value.lower() in ("true", "yes", "1", "on")
                elif isinstance(yaml_data[key], int):
                    try:
                        env_overrides[key] = int(env_value)
                    except ValueError:
                        pass
                elif isinstance(yaml_data[key], float):
                    try:
                        env_overrides[key] = float(env_value)
                    except ValueError:
                        pass
                else:
                    env_overrides[key] = env_value

        yaml_data.update(env_overrides)

        try:
            self._config = AppConfig(**yaml_data)
        except Exception as e:
            print(f"설정 검증 실패: {e}, 기본값 사용")
            self._config = AppConfig()

    def get(self) -> AppConfig:
        if self._config is None:
            self._load_config()
        assert self._config is not None, "AppConfig 초기화 실패"
        return self._config

    def reload(self) -> None:
        self._load_config()


_config_manager = ConfigManager()


def get_config() -> AppConfig:
    return _config_manager.get()
