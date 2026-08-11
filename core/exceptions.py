"""
core/exceptions.py - v5.6.0 FINAL (커스텀 예외 정의)
- 도메인별 세분화된 예외 클래스 제공
- 디버깅 및 오류 추적 용이
"""
from typing import Optional  # 🔥 이 줄이 누락되었습니다.


class KiwoomError(Exception):
    """키움 API 관련 기본 예외"""
    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


class KiwoomAuthError(KiwoomError):
    """인증 관련 예외 (토큰, IP, 권한)"""
    pass


class KiwoomWebSocketError(KiwoomError):
    """WebSocket 연결 관련 예외"""
    pass


class KiwoomRateLimitError(KiwoomError):
    """Rate Limit 초과 예외"""
    pass


class KiwoomDataError(KiwoomError):
    """데이터 처리 관련 예외"""
    pass


class KiwoomTokenExpiredError(KiwoomAuthError):
    """토큰 만료 예외 (특수 케이스)"""
    def __init__(self, message: str = "Access Token이 만료되었습니다"):
        super().__init__(message, code="100013")


class ConfigError(Exception):
    """설정 관련 예외"""
    pass


class DatabaseError(Exception):
    """데이터베이스 관련 예외"""
    pass


class TelegramError(Exception):
    """텔레그램 관련 예외"""
    pass


class WebSocketConnectionError(Exception):
    """WebSocket 연결 실패 예외"""
    pass


class WebSocketAuthError(Exception):
    """WebSocket 인증 실패 예외"""
    pass