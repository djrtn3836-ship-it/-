"""
data/db_manager.py - v5.4.6 (디버그 관제탑 적용)
- 🔥 수정: __init__에서 db_path를 Path로 변환하여 parent 속성 오류 방지
- 🔥 디버그 관제탑 적용 (debug_tower.log / capture_snapshot)
"""

import json
import aiosqlite
from pathlib import Path
from typing import List, Dict, Optional

from core.logger import setup_logger
from core.debug_tower import debug_tower   # 🔥 디버그 관제탑

logger = setup_logger("db_manager")
DB_PATH = Path(__file__).parent.parent / "data" / "decisions.db"


class DatabaseManager:
    def __init__(self, db_path: Path = DB_PATH):
        if isinstance(db_path, str):
            db_path = Path(db_path)
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
                    sentiment_score REAL DEFAULT 0.0,
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
            try:
                await db.execute("ALTER TABLE decisions ADD COLUMN sentiment_score REAL DEFAULT 0.0")
                await db.commit()
                logger.info("✅ decisions 테이블에 sentiment_score 컬럼 추가 완료")
            except Exception as e:
                if "duplicate column name" not in str(e).lower():
                    logger.warning(f"⚠️ sentiment_score 컬럼 추가 시도 중 오류: {e}")

            await db.execute("CREATE INDEX IF NOT EXISTS idx_decisions_created_at ON decisions(created_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_decisions_ticker ON decisions(ticker)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_decisions_action ON decisions(action)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_date ON ohlcv(ticker, date)")
            await db.commit()
            logger.info("✅ DB 초기화 완료 (OHLCV + sentiment_score 포함)")
            debug_tower.log("SYSTEM", "DB_INIT_DONE", {})

    # ============================================================
    # OHLCV 저장/조회
    # ============================================================
    async def save_ohlcv(self, ticker: str, date: str, ohlcv: dict):
        debug_tower.log(ticker, "DB_SAVE_OHLCV", {"date": date})
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
        debug_tower.log(ticker, "DB_GET_OHLCV", {"period": period})
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT date, open, high, low, close, volume FROM ohlcv WHERE ticker = ? ORDER BY date DESC LIMIT ?",
                (ticker, period)
            ) as cursor:
                rows = await cursor.fetchall()
                result = [dict(row) for row in rows]
                result.reverse()
                return result

    # ============================================================
    # 결정 기록 저장/조회
    # ============================================================
    async def save_decision(self, analysis: dict):
        ticker = analysis.get('ticker', 'UNKNOWN')
        debug_tower.log(ticker, "DB_SAVE_DECISION", {"action": analysis.get('action')})
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO decisions 
                (ticker, action, score, confidence, price_at_decision, positives, negatives, counterfactuals, sentiment_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                analysis.get('ticker', 'N/A'),
                analysis.get('action', 'HOLD'),
                analysis.get('score', 0.0),
                analysis.get('confidence', 0.0),
                analysis.get('price', 0.0),
                json.dumps(analysis.get('positives', []), ensure_ascii=False),
                json.dumps(analysis.get('negatives', []), ensure_ascii=False),
                json.dumps(analysis.get('counterfactuals', []), ensure_ascii=False),
                analysis.get('sentiment_score', 0.0)
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

    async def close(self):
        if hasattr(self, '_conn') and self._conn:
            try:
                await self._conn.close()
            except:
                pass
        logger.info("🔌 DB 연결 종료 완료")

    # ============================================================
    # 피드백 통계 조회
    # ============================================================
    async def get_feedback_stats(self, days: int = 30) -> Dict:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT d.action, o.return_1d, o.is_correct 
                   FROM decisions d 
                   JOIN decision_outcomes o ON d.id = o.decision_id 
                   WHERE d.created_at >= datetime('now', ?) 
                   AND o.is_correct IS NOT NULL""",
                (f'-{days} days',)
            ) as cursor:
                rows = await cursor.fetchall()
                if not rows:
                    return {"win_rate": 0.5, "sharpe": 1.0, "sample_count": 0, "avg_return": 0.0}

                correct = sum(1 for r in rows if r['is_correct'])
                total = len(rows)
                win_rate = correct / total if total > 0 else 0.5

                returns = [r['return_1d'] for r in rows if r['return_1d'] is not None]
                avg_ret = sum(returns) / len(returns) if returns else 0
                std_dev = (sum((r - avg_ret)**2 for r in returns) / len(returns)) ** 0.5 if returns else 1.0
                sharpe = (avg_ret / std_dev) * (252 ** 0.5) if std_dev > 0 else 0

                return {
                    "win_rate": round(win_rate, 3),
                    "sharpe": round(sharpe, 3),
                    "sample_count": total,
                    "avg_return": round(avg_ret, 3)
                }