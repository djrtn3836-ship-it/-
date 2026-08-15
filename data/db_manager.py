"""
data/db_manager.py - v5.4.2 (OHLCV 저장/조회 완전 구현)
"""
import json
import aiosqlite
from pathlib import Path
from typing import List, Dict, Optional
from core.logger import setup_logger

logger = setup_logger("db_manager")
DB_PATH = Path(__file__).parent.parent / "data" / "decisions.db"

class DatabaseManager:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    action TEXT NOT NULL,
                    score REAL,
                    confidence REAL,
                    price_at_decision REAL,
                    positives TEXT,
                    negatives TEXT,
                    counterfactuals TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS decision_outcomes (
                    decision_id INTEGER PRIMARY KEY,
                    price_after_1d REAL,
                    price_after_5d REAL,
                    return_1d REAL,
                    return_5d REAL,
                    is_correct BOOLEAN,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (decision_id) REFERENCES decisions(id)
                );
                CREATE TABLE IF NOT EXISTS feedback_weights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    factor_name TEXT UNIQUE NOT NULL,
                    weight REAL DEFAULT 1.0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                -- 🔥 OHLCV 테이블 (시가/고가/저가/종가/거래량)
                CREATE TABLE IF NOT EXISTS ohlcv (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(ticker, date)
                );
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_decisions_created_at ON decisions(created_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_decisions_ticker ON decisions(ticker)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_decisions_action ON decisions(action)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_date ON ohlcv(ticker, date)")
            await db.commit()
            logger.info("✅ DB 초기화 완료 (OHLCV 테이블 포함)")

    # ============================================================
    # 🔥 OHLCV 저장/조회 (ATR 계산용)
    # ============================================================
    async def save_ohlcv(self, ticker: str, date: str, ohlcv: dict):
        """OHLCV 데이터 저장 (시가/고가/저가/종가/거래량)"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO ohlcv (ticker, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker, date,
                ohlcv.get('open', 0.0),
                ohlcv.get('high', 0.0),
                ohlcv.get('low', 0.0),
                ohlcv.get('close', 0.0),
                ohlcv.get('volume', 0)
            ))
            await db.commit()

    async def get_ohlcv(self, ticker: str, period: int = 14) -> List[Dict]:
        """
        최근 N일 OHLCV 데이터 조회 (ATR 계산용)
        Returns: [{'date': '2026-08-01', 'open': 100, 'high': 110, 'low': 95, 'close': 105, 'volume': 1000}, ...]
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT date, open, high, low, close, volume FROM ohlcv WHERE ticker = ? ORDER BY date DESC LIMIT ?",
                (ticker, period)
            ) as cursor:
                rows = await cursor.fetchall()
                # 날짜 오름차순으로 반환 (ATR 계산은 시간 순서 필요)
                result = [dict(row) for row in rows]
                result.reverse()
                return result

    # ============================================================
    # 기존 메서드 (save_decision, get_decisions_by_date 등)
    # ============================================================
    async def save_decision(self, analysis: dict):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO decisions 
                (ticker, action, score, confidence, price_at_decision, positives, negatives, counterfactuals)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                analysis.get('ticker', 'N/A'),
                analysis.get('action', 'HOLD'),
                analysis.get('score', 0.0),
                analysis.get('confidence', 0.0),
                analysis.get('price', 0.0),
                json.dumps(analysis.get('positives', []), ensure_ascii=False),
                json.dumps(analysis.get('negatives', []), ensure_ascii=False),
                json.dumps(analysis.get('counterfactuals', []), ensure_ascii=False)
            ))
            await db.commit()

    async def get_decisions_by_date(self, date_str: str) -> list:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM decisions WHERE DATE(created_at) = ? ORDER BY created_at DESC",
                (date_str,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_weights(self) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT factor_name, weight FROM feedback_weights") as cursor:
                rows = await cursor.fetchall()
                weights = {row['factor_name']: row['weight'] for row in rows}
                default_factors = ['momentum', 'volume', 'volatility', 'macro', 'sector']
                for f in default_factors:
                    if f not in weights:
                        weights[f] = 1.0
                return weights

    async def update_weight(self, factor_name: str, new_weight: float):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO feedback_weights (factor_name, weight, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (factor_name, new_weight))
            await db.commit()

    async def save_outcome(self, outcome: dict):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO decision_outcomes 
                (decision_id, price_after_1d, price_after_5d, return_1d, return_5d, is_correct)
                VALUES (:decision_id, :price_after_1d, :price_after_5d, :return_1d, :return_5d, :is_correct)
            """, outcome)
            await db.commit()
    # ============================================================
    # 🔥 진단 스크립트 호환을 위한 close 메서드 추가
    # ============================================================
    async def close(self):
        """데이터베이스 연결 종료 (진단 스크립트 호환용)"""
        # SQLite는 연결 종료가 따로 필요 없지만, 메서드 존재 여부만으로 오류를 피하기 위해 정의
        if hasattr(self, '_conn') and self._conn:
            try:
                await self._conn.close()
            except:
                pass
        logger.info("🔌 DB 연결 종료 완료")