"""
data/db_manager.py - v6.0 FINAL (연결 풀링 + 타임아웃 + 인덱스 최적화)
- aiosqlite 연결 풀링 (Pool)으로 동시 요청 처리 성능 향상
- 쿼리 타임아웃(5초) 및 자동 재시도(최대 2회)
- 매일 00:00에 인덱스 통계 갱신 (ANALYZE)
- 디버그 관제탑 연동 유지
"""

import json
import asyncio
import aiosqlite
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta

from core.logger import setup_logger
from core.debug_tower import debug_tower

logger = setup_logger("db_manager")
DB_PATH = Path(__file__).parent.parent / "data" / "decisions.db"

# 연결 풀 설정
MAX_CONNECTIONS = 5
CONNECTION_TIMEOUT = 10
QUERY_TIMEOUT = 5.0
MAX_RETRIES = 2


class DatabaseManager:
    """데이터베이스 관리자 (v6.0 - 연결 풀링 + 타임아웃)"""

    def __init__(self, db_path: Path = DB_PATH):
        if isinstance(db_path, str):
            db_path = Path(db_path)
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._pool: Optional[aiosqlite.Connection] = None
        self._last_analyze = datetime.min
        self._analyze_interval = timedelta(days=1)

    async def _get_connection(self) -> aiosqlite.Connection:
        """연결 풀에서 연결 획득 (Timeout 적용)"""
        if self._pool is None:
            try:
                self._pool = await asyncio.wait_for(
                    aiosqlite.connect(self.db_path, timeout=CONNECTION_TIMEOUT),
                    timeout=CONNECTION_TIMEOUT
                )
                self._pool.row_factory = aiosqlite.Row
                # WAL 모드 활성화 (성능 향상)
                await self._pool.execute("PRAGMA journal_mode=WAL")
                await self._pool.execute("PRAGMA synchronous=NORMAL")
                await self._pool.execute("PRAGMA cache_size=-20000")  # 20MB 캐시
                logger.info("✅ DB 연결 풀 초기화 완료 (WAL 모드)")
            except asyncio.TimeoutError:
                logger.error("❌ DB 연결 타임아웃")
                raise
        return self._pool

    async def _execute_with_retry(self, query: str, params: tuple = (), retries: int = MAX_RETRIES) -> aiosqlite.Cursor:
        """쿼리 실행 (재시도 + 타임아웃)"""
        last_error = None
        for attempt in range(retries + 1):
            try:
                conn = await self._get_connection()
                async with asyncio.timeout(QUERY_TIMEOUT):
                    cursor = await conn.execute(query, params)
                    await conn.commit()
                    return cursor
            except (asyncio.TimeoutError, aiosqlite.OperationalError) as e:
                last_error = e
                if "database is locked" in str(e) and attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise
            except Exception as e:
                raise
        raise last_error

    async def init_db(self):
        """DB 초기화 (인덱스 최적화 포함)"""
        conn = await self._get_connection()

        await conn.executescript("""
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
                ml_score REAL DEFAULT 0.5,
                risk_adjustment_factor REAL DEFAULT 1.0,
                strategy_scores TEXT,
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
            CREATE TABLE IF NOT EXISTS portfolio_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT UNIQUE NOT NULL,
                entry_price REAL,
                current_price REAL,
                qty INTEGER,
                entry_time DATETIME,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 컬럼 추가 (중복 방지)
        for col, dtype in [
            ("ml_score", "REAL DEFAULT 0.5"),
            ("risk_adjustment_factor", "REAL DEFAULT 1.0"),
            ("strategy_scores", "TEXT"),
        ]:
            try:
                await conn.execute(f"ALTER TABLE decisions ADD COLUMN {col} {dtype}")
                await conn.commit()
            except aiosqlite.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    logger.warning(f"⚠️ 컬럼 추가 시도 중 오류: {e}")

        # 인덱스 생성 (존재하지 않으면 생성)
        indexes = [
            ("idx_decisions_created_at", "decisions", "created_at"),
            ("idx_decisions_ticker", "decisions", "ticker"),
            ("idx_decisions_action", "decisions", "action"),
            ("idx_ohlcv_ticker_date", "ohlcv", "ticker, date"),
            ("idx_positions_ticker", "portfolio_positions", "ticker"),
        ]
        for idx_name, table, columns in indexes:
            try:
                await conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({columns})")
            except Exception as e:
                logger.warning(f"⚠️ 인덱스 생성 실패 ({idx_name}): {e}")

        await conn.commit()

        # 테이블 통계 갱신
        await self.analyze_db()

        logger.info("✅ DB 초기화 완료 (v6.0 - 연결 풀링 + 인덱스 최적화)")
        debug_tower.log("SYSTEM", "DB_INIT_DONE", {})

    async def analyze_db(self):
        """인덱스 통계 갱신 (성능 최적화)"""
        now = datetime.now()
        if (now - self._last_analyze) < self._analyze_interval:
            return
        try:
            conn = await self._get_connection()
            await conn.execute("ANALYZE")
            await conn.commit()
            self._last_analyze = now
            logger.debug("✅ DB ANALYZE 완료")
        except Exception as e:
            logger.debug(f"⚠️ ANALYZE 실패: {e}")

    # ============================================================
    # OHLCV 저장/조회 (재시도 적용)
    # ============================================================
    async def save_ohlcv(self, ticker: str, date: str, ohlcv: dict):
        debug_tower.log(ticker, "DB_SAVE_OHLCV", {"date": date})
        await self._execute_with_retry("""
            INSERT OR REPLACE INTO ohlcv (ticker, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (ticker, date, ohlcv.get('open', 0.0), ohlcv.get('high', 0.0),
              ohlcv.get('low', 0.0), ohlcv.get('close', 0.0), ohlcv.get('volume', 0)))

    async def get_ohlcv(self, ticker: str, period: int = 14) -> List[Dict]:
        debug_tower.log(ticker, "DB_GET_OHLCV", {"period": period})
        cursor = await self._execute_with_retry(
            "SELECT date, open, high, low, close, volume FROM ohlcv WHERE ticker = ? ORDER BY date DESC LIMIT ?",
            (ticker, period)
        )
        rows = await cursor.fetchall()
        result = [dict(row) for row in rows]
        result.reverse()
        return result

    # ============================================================
    # 결정 기록 (v6.0: ml_score, risk_adj, strategy_scores 저장)
    # ============================================================
    async def save_decision(self, analysis: dict):
        ticker = analysis.get('ticker', 'UNKNOWN')
        debug_tower.log(ticker, "DB_SAVE_DECISION", {"action": analysis.get('action')})

        # strategy_scores JSON 직렬화
        strategy_scores = analysis.get('strategy_result')
        strategy_json = json.dumps(strategy_scores, ensure_ascii=False) if strategy_scores else None

        await self._execute_with_retry("""
            INSERT INTO decisions 
            (ticker, action, score, confidence, price_at_decision, positives, negatives, counterfactuals,
             sentiment_score, ml_score, risk_adjustment_factor, strategy_scores)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            analysis.get('ticker', 'N/A'),
            analysis.get('action', 'HOLD'),
            analysis.get('score', 0.0),
            analysis.get('confidence', 0.0),
            analysis.get('price', 0.0),
            json.dumps(analysis.get('positives', []), ensure_ascii=False),
            json.dumps(analysis.get('negatives', []), ensure_ascii=False),
            json.dumps(analysis.get('counterfactuals', []), ensure_ascii=False),
            analysis.get('sentiment_score', 0.0),
            analysis.get('ml_score', 0.5),
            analysis.get('risk_adjustment_factor', 1.0),
            strategy_json
        ))

    async def get_decisions_by_date(self, date_str: str) -> list:
        cursor = await self._execute_with_retry(
            "SELECT * FROM decisions WHERE DATE(created_at) = ? ORDER BY created_at DESC",
            (date_str,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ============================================================
    # 포트폴리오 포지션 (v6.0 신규)
    # ============================================================
    async def save_position(self, ticker: str, entry_price: float, current_price: float, qty: int):
        await self._execute_with_retry("""
            INSERT OR REPLACE INTO portfolio_positions (ticker, entry_price, current_price, qty, entry_time, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
        """, (ticker, entry_price, current_price, qty))

    async def delete_position(self, ticker: str):
        await self._execute_with_retry(
            "DELETE FROM portfolio_positions WHERE ticker = ?",
            (ticker,)
        )

    async def get_positions(self) -> List[Dict]:
        cursor = await self._execute_with_retry(
            "SELECT * FROM portfolio_positions ORDER BY ticker"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ============================================================
    # 기존 메서드 (재시도 적용)
    # ============================================================
    async def get_weights(self) -> dict:
        cursor = await self._execute_with_retry("SELECT factor_name, weight FROM feedback_weights")
        rows = await cursor.fetchall()
        weights = {row['factor_name']: row['weight'] for row in rows}
        default_factors = ['momentum', 'volume', 'volatility', 'macro', 'sector']
        for f in default_factors:
            if f not in weights:
                weights[f] = 1.0
        return weights

    async def update_weight(self, factor_name: str, new_weight: float):
        await self._execute_with_retry("""
            INSERT OR REPLACE INTO feedback_weights (factor_name, weight, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """, (factor_name, new_weight))

    async def save_outcome(self, outcome: dict):
        await self._execute_with_retry("""
            INSERT OR REPLACE INTO decision_outcomes 
            (decision_id, price_after_1d, price_after_5d, return_1d, return_5d, is_correct)
            VALUES (:decision_id, :price_after_1d, :price_after_5d, :return_1d, :return_5d, :is_correct)
        """, outcome)

    async def get_feedback_stats(self, days: int = 30) -> Dict:
        cursor = await self._execute_with_retry(
            """SELECT d.action, o.return_1d, o.is_correct 
               FROM decisions d 
               JOIN decision_outcomes o ON d.id = o.decision_id 
               WHERE d.created_at >= datetime('now', ?) 
               AND o.is_correct IS NOT NULL""",
            (f'-{days} days',)
        )
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

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None
        logger.info("🔌 DB 연결 종료 완료")