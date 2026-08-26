"""
data/base_db.py - v1.0 (P3-5: Database Abstraction Layer)
- 모든 DB 구현체가 따라야 할 추상 인터페이스
- SQLite/PostgreSQL 전환을 위한 표준화
"""

from abc import ABC, abstractmethod



class BaseDBManager(ABC):
    """데이터베이스 관리자 추상 클래스"""

    # ============================================================
    # 초기화
    # ============================================================
    @abstractmethod
    async def init_db(self) -> None:
        """DB 초기화 (테이블 생성 등)"""
        pass

    @abstractmethod
    async def close(self) -> None:
        """DB 연결 종료"""
        pass

    # ============================================================
    # OHLCV
    # ============================================================
    @abstractmethod
    async def save_ohlcv(self, ticker: str, date: str, ohlcv: dict) -> None:
        """OHLCV 저장"""
        pass

    @abstractmethod
    async def save_ohlcv_batch(self, records: list[tuple]) -> int:
        """대량 OHLCV 저장"""
        pass

    @abstractmethod
    async def get_ohlcv(self, ticker: str, period: int = 14) -> list[dict]:
        """최근 N개 OHLCV 조회"""
        pass

    @abstractmethod
    async def get_ohlcv_range(self, ticker: str, start: str, end: str) -> list[dict]:
        """날짜 범위 OHLCV 조회"""
        pass

    # ============================================================
    # 결정(Decision)
    # ============================================================
    @abstractmethod
    async def save_decision(self, analysis: dict) -> None:
        """결정 저장"""
        pass

    @abstractmethod
    async def get_decisions_by_date(self, date_str: str) -> list[dict]:
        """특정 일자 결정 조회"""
        pass

    @abstractmethod
    async def get_decisions_by_date_range(self, start: str, end: str) -> list[dict]:
        """날짜 범위 결정 조회"""
        pass

    # ============================================================
    # 포트폴리오
    # ============================================================
    @abstractmethod
    async def save_position(self, ticker: str, entry_price: float, current_price: float, qty: int) -> None:
        """포지션 저장"""
        pass

    @abstractmethod
    async def delete_position(self, ticker: str) -> None:
        """포지션 삭제"""
        pass

    @abstractmethod
    async def get_positions(self) -> list[dict]:
        """모든 포지션 조회"""
        pass

    # ============================================================
    # 피드백/가중치
    # ============================================================
    @abstractmethod
    async def get_weights(self) -> dict:
        """피드백 가중치 조회"""
        pass

    @abstractmethod
    async def update_weight(self, factor_name: str, new_weight: float) -> None:
        """가중치 업데이트"""
        pass

    @abstractmethod
    async def save_outcome(self, outcome: dict) -> None:
        """결과 저장"""
        pass

    @abstractmethod
    async def get_feedback_stats(self, days: int = 30) -> dict:
        """피드백 통계 조회"""
        pass