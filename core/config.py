"""
core/config.py - v5.6.0 FINAL (통합 설정 관리자)
- 기존 ConfigManager 기능 확장
- YAML + .env 통합 관리
- 환경 변수 우선 적용
"""
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv

from core.logger import setup_logger
from core.exceptions import ConfigError

logger = setup_logger("config")


class ConfigManager:
    """통합 설정 관리자 (싱글톤)"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._config: Dict[str, Any] = {}
        self._config_dir = Path(__file__).parent.parent / "config"
        self._env_loaded = False
        
        load_dotenv()
        self._env_loaded = True
        self._load_defaults()
    
    def _load_defaults(self):
        """기본 설정 로드"""
        self._config = {
            # WebSocket
            "ws_url": "wss://api.kiwoom.com:10000/api/dostk/websocket",
            "ws_mock_url": "wss://mockapi.kiwoom.com:10000/api/dostk/websocket",
            "ws_ping_interval": 20,
            "ws_ping_timeout": 60,
            "ws_close_timeout": 10,
            "ws_login_timeout": 10,
            "ws_reg_timeout": 5,
            
            # Rate Limit
            "rate_limit_capacity": 5,
            "rate_limit_refill": 5.0,
            
            # Signal
            "price_change_ratio": 0.02,
            "cooldown_seconds": 300,
            "emergency_threshold": 0.05,
            "max_subscriptions": 50,
            
            # Scheduler
            "daily_report_hour": 7,
            "daily_report_minute": 0,
            "feedback_hour": 17,
            "feedback_minute": 0,
            "weekly_pdf_day": "mon",
            "weekly_pdf_hour": 6,
            "weekly_pdf_minute": 0,
            "ohlcv_hour": 16,
            "ohlcv_minute": 30,
            
            # Reconnect
            "reconnect_max_attempts": 5,
            "reconnect_base_delay": 2,
            "reconnect_max_delay": 60,
            "reconnect_interval": 30,
            
            # Queue
            "queue_maxsize": 10000,
            
            # Connect
            "connect_retry_interval": 60,
        }
        
        # YAML 파일 로드
        config_file = self._config_dir / "config.yaml"
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    yaml_config = yaml.safe_load(f)
                    if yaml_config:
                        self._update_from_dict(yaml_config)
                        logger.info(f"✅ YAML 설정 로드 완료: {config_file}")
            except Exception as e:
                logger.warning(f"⚠️ YAML 설정 로드 실패: {e}")
    
    def _update_from_dict(self, data: Dict, prefix: str = ""):
        """딕셔너리로 설정 업데이트"""
        for key, value in data.items():
            full_key = f"{prefix}{key}" if prefix else key
            if isinstance(value, dict):
                self._update_from_dict(value, f"{full_key}_")
            else:
                self._config[full_key] = value
    
    def get(self, key: str, default: Any = None) -> Any:
        """설정값 조회 (환경 변수 우선)"""
        env_key = key.upper()
        env_value = os.getenv(env_key)
        if env_value is not None:
            return env_value
        return self._config.get(key, default)
    
    def get_int(self, key: str, default: int = 0) -> int:
        """정수형 설정 조회"""
        value = self.get(key, default)
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    
    def get_float(self, key: str, default: float = 0.0) -> float:
        """실수형 설정 조회"""
        value = self.get(key, default)
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
    
    def get_bool(self, key: str, default: bool = False) -> bool:
        """불리언 설정 조회"""
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "yes", "1", "on")
        return bool(value)


# 싱글톤 인스턴스
_config_manager: Optional[ConfigManager] = None


def get_config() -> ConfigManager:
    """설정 매니저 싱글톤 반환"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def reload_config() -> ConfigManager:
    """설정 재로드"""
    global _config_manager
    _config_manager = None
    return get_config()