"""
core/settings.py - v5.6.0 FINAL (중앙 설정 관리)
- 모든 설정 값을 dataclass로 중앙 관리
- YAML + .env 병합 지원
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from core.logger import setup_logger

logger = setup_logger("settings")


@dataclass
class WebSocketConfig:
    """WebSocket 관련 설정"""

    url: str = "wss://api.kiwoom.com:10000/api/dostk/websocket"
    mock_url: str = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"
    ping_interval: int = 20
    ping_timeout: int = 60
    close_timeout: int = 10
    login_timeout: int = 10
    reg_timeout: int = 5


@dataclass
class RateLimitConfig:
    """Rate Limit 설정"""

    capacity: int = 5
    refill_rate: float = 5.0


@dataclass
class SignalConfig:
    """신호 감지 설정"""

    price_change_ratio: float = 0.02
    cooldown_seconds: int = 300
    emergency_threshold: float = 0.05
    max_subscriptions: int = 50


@dataclass
class DatabaseConfig:
    """데이터베이스 설정"""

    path: str = "data/decisions.db"
    ohlcv_table: str = "ohlcv"
    decisions_table: str = "decisions"


@dataclass
class TelegramConfig:
    """텔레그램 설정"""

    bot_token: str | None = None
    chat_id: str | None = None


@dataclass
class SchedulerConfig:
    """스케줄러 설정"""

    daily_report_hour: int = 7
    daily_report_minute: int = 0
    feedback_learning_hour: int = 17
    feedback_learning_minute: int = 0
    weekly_pdf_day: str = "mon"
    weekly_pdf_hour: int = 6
    weekly_pdf_minute: int = 0
    ohlcv_collect_hour: int = 16
    ohlcv_collect_minute: int = 30
    retry_max_attempts: int = 3
    retry_delay_seconds: int = 60


@dataclass
class ReconnectConfig:
    """재연결 설정"""

    max_attempts: int = 5
    base_delay: int = 2
    max_delay: int = 60
    reconnect_interval: int = 30


@dataclass
class Settings:
    """통합 설정"""

    websocket: WebSocketConfig = field(default_factory=WebSocketConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)

    env_loaded: bool = False

    @classmethod
    def load(cls, config_path: Path | None = None) -> "Settings":
        """설정 로드 (.env + YAML)"""
        load_dotenv()

        settings = cls()
        settings.env_loaded = True

        # .env에서 Telegram 설정 로드
        settings.telegram.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        settings.telegram.chat_id = os.getenv("TELEGRAM_CHAT_ID")

        # YAML 파일이 있으면 로드 (선택)
        if config_path and config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if data:
                        settings._update_from_dict(data)
                        logger.info(f"✅ YAML 설정 로드 완료: {config_path}")
            except Exception as e:
                logger.warning(f"⚠️ YAML 설정 로드 실패: {e}")

        return settings

    def _update_from_dict(self, data: dict, prefix: str = ""):
        """딕셔너리로 설정 업데이트 (재귀)"""
        for key, value in data.items():
            full_key = f"{prefix}{key}" if prefix else key
            if isinstance(value, dict):
                self._update_from_dict(value, f"{full_key}_")
            else:
                if hasattr(self, full_key):
                    setattr(self, full_key, value)


# 싱글톤 인스턴스
_settings: Settings | None = None


def get_settings() -> Settings:
    """싱글톤 설정 인스턴스 반환"""
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings


def reload_settings() -> Settings:
    """설정 재로드"""
    global _settings
    _settings = None
    return get_settings()
