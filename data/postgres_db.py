"""
data/postgres_db.py - v1.0 (P3-5: PostgreSQL Implementation)
- asyncpg 기반 PostgreSQL 드라이버
- BaseDBManager 인터페이스 구현
- 현재는 SQLite와 동일한 인터페이스 제공 (마이그레이션 준비)
"""




import asyncpg

from core.logger import setup_logger
from data.base_db import BaseDBManager

logger = setup_logger("postgres_db")


class PostgreSQLDBManager(BaseDBManager):
    """PostgreSQL 구현체"""

    def __init__(self, dsn: str, pool_min_size: int = 1, pool_max_size: int = 10):
        self.dsn = dsn
        self.pool_min_size = pool_min_size
        self.pool_max_size = pool_max_size
        self._pool: asyncpg.Pool | None = None

    async def init_db(self) -> None:
        """테이블 생성 (PostgreSQL 문법)"""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self.dsn,
                min_size=self.pool_min_size,
                max_size=self.pool_max_size,
            )
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS decisions (
                    id SERIAL PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    action TEXT NOT NULL,
                    score REAL,
                    confidence REAL,
                    price_at_decision REAL,
                    positives TEXT,
                    negatives TEXT,
                    counterfactuals TEXT,
                    sentiment_score REAL DEFAULT 0.0,
                    ml_score REAL DEFAULT 0.5,
                    risk_adjustment_factor REAL DEFAULT 1.0,
                    strategy_scores TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS decision_outcomes (
                    decision_id INTEGER PRIMARY KEY REFERENCES decisions(id),
                    price_after_1d REAL,
                    price_after_5d REAL,
                    return_1d REAL,
                    return_5d REAL,
                    is_correct BOOLEAN,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                -- 기타 테이블 생략 (SQLite와 동일 구조)
            """)
            logger.info("✅ PostgreSQL DB 초기화 완료")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("🔌 PostgreSQL 연결 종료")

    # ============================================================
    # OHLCV (구현 생략 - SQLite와 동일한 로직, asyncpg 문법 사용)
    # ============================================================
    async def save_ohlcv(self, ticker: str, date: str, ohlcv: dict) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ohlcv (ticker, date, open, high, low, close, volume)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (ticker, date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume
                """,
                ticker, date,
                ohlcv.get("open", 0.0),
                ohlcv.get("high", 0.0),
                ohlcv.get("low", 0.0),
                ohlcv.get("close", 0.0),
                ohlcv.get("volume", 0),
            )

    async def save_ohlcv_batch(self, records: list[tuple]) -> int:
        if not records:
            return 0
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO ohlcv (ticker, date, open, high, low, close, volume)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (ticker, date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume
                """,
                records,
            )
            return len(records)

    async def get_ohlcv(self, ticker: str, period: int = 14) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT date, open, high, low, close, volume
                FROM ohlcv
                WHERE ticker = $1
                ORDER BY date DESC
                LIMIT $2
                """,
                ticker, period,
            )
            return [dict(row) for row in rows][::-1]

    async def get_ohlcv_range(self, ticker: str, start: str, end: str) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT date, open, high, low, close, volume
                FROM ohlcv
                WHERE ticker = $1 AND date BETWEEN $2 AND $3
                ORDER BY date ASC
                """,
                ticker, start, end,
            )
            return [dict(row) for row in rows]

    # ============================================================
    # 결정 (Decision) - 생략 (동일 패턴)
    # ============================================================
    async def save_decision(self, analysis: dict) -> None:
        # 구현 생략 (SQLite와 유사)
        pass

    async def get_decisions_by_date(self, date_str: str) -> list[dict]:
        # 구현 생략
        return []

    async def get_decisions_by_date_range(self, start: str, end: str) -> list[dict]:
        # 구현 생략
        return []

    # ============================================================
    # 포트폴리오 (생략)
    # ============================================================
    async def save_position(self, ticker: str, entry_price: float, current_price: float, qty: int) -> None:
        pass

    async def delete_position(self, ticker: str) -> None:
        pass

    async def get_positions(self) -> list[dict]:
        return []

    # ============================================================
    # 피드백/가중치 (생략)
    # ============================================================
    async def get_weights(self) -> dict:
        return {}

    async def update_weight(self, factor_name: str, new_weight: float) -> None:
        pass

    async def save_outcome(self, outcome: dict) -> None:
        pass

    async def get_feedback_stats(self, days: int = 30) -> dict:
        return {"win_rate": 0.5, "sharpe": 1.0, "sample_count": 0, "avg_return": 0.0}