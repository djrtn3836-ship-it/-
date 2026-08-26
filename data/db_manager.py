# -*- coding: utf-8 -*-
"""
data/db_manager.py - v6.1.3 (OHLCV 배치 커밋 + get_outcome 추가)
- save_ohlcv()도 _execute_batched 사용하도록 변경
- save_ohlcv_batch() 신규 추가 (대량 저장 최적화)
- get_outcome() 추가: validation/backtester.py의 Walk-Forward 검증에서
  승률이 항상 0.0으로 계산되던 버그 해결
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from core.debug_tower import debug_tower
from core.logger import setup_logger

logger = setup_logger("db_manager")
DB_PATH = Path(__file__).parent.parent / "data" / "decisions.db"

MAX_CONNECTIONS = 5
CONNECTION_TIMEOUT = 10
QUERY_TIMEOUT = 5.0
MAX_RETRIES = 2


class DatabaseManager:
    def __init__(self, db_path: Path = DB_PATH):
        if isinstance(db_path, str):
            db_path = Path(db_path)
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._pool: aiosqlite.Connection | None = None
        self._last_analyze = datetime.min
        self._analyze_interval = timedelta(days=1)

        self._pending_writes = 0
        self._batch_size = 20
        self._commit_interval = 0.5
        self._last_commit_time = time.time()

    async def _get_connection(self) -> aiosqlite.Connection:
        if self._pool is None:
            try:
                self._pool = await asyncio.wait_for(
                    aiosqlite.connect(self.db_path, timeout=CONNECTION_TIMEOUT), timeout=CONNECTION_TIMEOUT
                )
                self._pool.row_factory = aiosqlite.Row
                await self._pool.execute("PRAGMA journal_mode=WAL")
                await self._pool.execute("PRAGMA synchronous=NORMAL")
                await self._pool.execute("PRAGMA cache_size=-20000")
                logger.info("DB 연결 풀 초기화 완료 (WAL 모드)")
            except TimeoutError:
                logger.error("DB 연결 타임아웃")
                raise
        return self._pool

    async def _execute_with_retry(self, query: str, params: tuple = (), retries: int = MAX_RETRIES) -> aiosqlite.Cursor:
        last_error = None
        for attempt in range(retries + 1):
            try:
                conn = await self._get_connection()
                async with asyncio.timeout(QUERY_TIMEOUT):
                    cursor = await conn.execute(query, params)
                    await conn.commit()
                    return cursor
            except (TimeoutError, aiosqlite.OperationalError) as e:
                last_error = e
                if "database is locked" in str(e) and attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise
            except Exception:
                raise
        raise last_error

    async def _execute_batched(self, query: str, params: tuple = ()) -> aiosqlite.Cursor:
        conn = await self._get_connection()
        cursor = await conn.execute(query, params)
        self._pending_writes += 1

        now = time.time()
        if self._pending_writes >= self._batch_size or (now - self._last_commit_time) >= self._commit_interval:
            await conn.commit()
            self._pending_writes = 0
            self._last_commit_time = now

        return cursor

    async def _flush_pending(self):
        if self._pending_writes > 0:
            conn = await self._get_connection()
            await conn.commit()
            self._pending_writes = 0
            logger.debug("배치 커밋 플러시 완료")

    async def init_db(self):
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
            CREATE TABLE IF NOT EXISTS trailing_stop_states (
                ticker TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                saved_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

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
                    logger.warning(f"컬럼 추가 시도 중 오류: {e}")

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
                logger.warning(f"인덱스 생성 실패 ({idx_name}): {e}")

        await conn.commit()
        await self.analyze_db()

        logger.info("DB 초기화 완료 (v6.2.0 - trailing_stop_states 테이블 추가)")
        debug_tower.log("SYSTEM", "DB_INIT_DONE", {})

    async def analyze_db(self):
        now = datetime.now()
        if (now - self._last_analyze) < self._analyze_interval:
            return
        try:
            conn = await self._get_connection()
            await conn.execute("ANALYZE")
            await conn.commit()
            self._last_analyze = now
            logger.debug("DB ANALYZE 완료")
        except Exception as e:
            logger.debug(f"ANALYZE 실패: {e}")

    async def save_ohlcv(self, ticker: str, date: str, ohlcv: dict):
        """OHLCV 저장 (배치 커밋 적용)"""
        debug_tower.log(ticker, "DB_SAVE_OHLCV", {"date": date})
        await self._execute_batched(
            """
            INSERT OR REPLACE INTO ohlcv (ticker, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                ticker,
                date,
                ohlcv.get("open", 0.0),
                ohlcv.get("high", 0.0),
                ohlcv.get("low", 0.0),
                ohlcv.get("close", 0.0),
                ohlcv.get("volume", 0),
            ),
        )

    async def save_ohlcv_batch(self, records: list[tuple]) -> int:
        if not records:
            return 0
        conn = await self._get_connection()
        await conn.executemany(
            """
            INSERT OR REPLACE INTO ohlcv (ticker, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            records,
        )
        await conn.commit()
        logger.debug(f"OHLCV 배치 저장 완료: {len(records)}개 레코드")
        return len(records)

    async def get_ohlcv(self, ticker: str, period: int = 14) -> list[dict]:
        debug_tower.log(ticker, "DB_GET_OHLCV", {"period": period})
        cursor = await self._execute_with_retry(
            "SELECT date, open, high, low, close, volume FROM ohlcv WHERE ticker = ? ORDER BY date DESC LIMIT ?",
            (ticker, period),
        )
        rows = await cursor.fetchall()
        result = [dict(row) for row in rows]
        result.reverse()
        return result

    async def get_ohlcv_range(self, ticker: str, start_date: str, end_date: str) -> list[dict]:
        cursor = await self._execute_with_retry(
            """SELECT date, open, high, low, close, volume
               FROM ohlcv
               WHERE ticker = ? AND date >= ? AND date <= ?
               ORDER BY date ASC""",
            (ticker, start_date, end_date),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def save_decision(self, analysis: dict):
        features = analysis.pop("features", {})
        strategy_scores = analysis.get("strategy_result")

        combined_json = {}
        if strategy_scores:
            combined_json["scores"] = strategy_scores
        if features:
            combined_json["features"] = features
        strategy_json = json.dumps(combined_json, ensure_ascii=False) if combined_json else None

        ticker = analysis.get("ticker", "UNKNOWN")
        debug_tower.log(ticker, "DB_SAVE_DECISION", {"action": analysis.get("action")})

        await self._execute_batched(
            """
            INSERT INTO decisions
            (ticker, action, score, confidence, price_at_decision, positives, negatives, counterfactuals,
             sentiment_score, ml_score, risk_adjustment_factor, strategy_scores)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                analysis.get("ticker", "N/A"),
                analysis.get("action", "HOLD"),
                analysis.get("score", 0.0),
                analysis.get("confidence", 0.0),
                analysis.get("price", 0.0),
                json.dumps(analysis.get("positives", []), ensure_ascii=False),
                json.dumps(analysis.get("negatives", []), ensure_ascii=False),
                json.dumps(analysis.get("counterfactuals", []), ensure_ascii=False),
                analysis.get("sentiment_score", 0.0),
                analysis.get("ml_score", 0.5),
                analysis.get("risk_adjustment_factor", 1.0),
                strategy_json,
            ),
        )

    async def get_decisions_by_date(self, date_str: str) -> list:
        cursor = await self._execute_with_retry(
            "SELECT * FROM decisions WHERE DATE(created_at) = ? ORDER BY created_at DESC", (date_str,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_decisions_by_date_range(self, start_date: str, end_date: str) -> list[dict]:
        cursor = await self._execute_with_retry(
            """SELECT * FROM decisions
               WHERE DATE(created_at) >= ? AND DATE(created_at) <= ?
               ORDER BY created_at ASC""",
            (start_date, end_date),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def save_position(self, ticker: str, entry_price: float, current_price: float, qty: int):
        await self._execute_batched(
            """
            INSERT OR REPLACE INTO portfolio_positions (ticker, entry_price, current_price, qty, entry_time, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
            (ticker, entry_price, current_price, qty),
        )

    async def delete_position(self, ticker: str):
        await self._execute_batched("DELETE FROM portfolio_positions WHERE ticker = ?", (ticker,))

    async def get_positions(self) -> list[dict]:
        cursor = await self._execute_with_retry("SELECT * FROM portfolio_positions ORDER BY ticker")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_weights(self) -> dict:
        cursor = await self._execute_with_retry("SELECT factor_name, weight FROM feedback_weights")
        rows = await cursor.fetchall()
        weights = {row["factor_name"]: row["weight"] for row in rows}
        default_factors = ["momentum", "volume", "volatility", "macro", "sector"]
        for f in default_factors:
            if f not in weights:
                weights[f] = 1.0
        return weights

    async def update_weight(self, factor_name: str, new_weight: float):
        await self._execute_batched(
            """
            INSERT OR REPLACE INTO feedback_weights (factor_name, weight, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """,
            (factor_name, new_weight),
        )

    async def save_outcome(self, outcome: dict):
        await self._execute_batched(
            """
            INSERT OR REPLACE INTO decision_outcomes
            (decision_id, price_after_1d, price_after_5d, return_1d, return_5d, is_correct)
            VALUES (:decision_id, :price_after_1d, :price_after_5d, :return_1d, :return_5d, :is_correct)
        """,
            outcome,
        )

    async def get_outcome(self, decision_id: int) -> dict | None:
        """
        특정 decision_id에 대한 결과(outcome) 단건 조회.
        validation/backtester.py의 _train_on_period()에서 사용되며,
        이 메서드가 없어서 Walk-Forward 검증 승률이 항상 0.0으로
        계산되던 버그를 해결하기 위해 추가됨.
        """
        cursor = await self._execute_with_retry(
            "SELECT * FROM decision_outcomes WHERE decision_id = ?",
            (decision_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_feedback_stats(self, days: int = 30) -> dict:
        cursor = await self._execute_with_retry(
            """SELECT d.action, o.return_1d, o.is_correct
               FROM decisions d
               JOIN decision_outcomes o ON d.id = o.decision_id
               WHERE d.created_at >= datetime('now', ?)
               AND o.is_correct IS NOT NULL""",
            (f"-{days} days",),
        )
        rows = await cursor.fetchall()
        if not rows:
            return {"win_rate": 0.5, "sharpe": 1.0, "sample_count": 0, "avg_return": 0.0}
        correct = sum(1 for r in rows if r["is_correct"])
        total = len(rows)
        win_rate = correct / total if total > 0 else 0.5
        returns = [r["return_1d"] for r in rows if r["return_1d"] is not None]
        avg_ret = sum(returns) / len(returns) if returns else 0
        std_dev = (sum((r - avg_ret) ** 2 for r in returns) / len(returns)) ** 0.5 if returns else 1.0
        sharpe = (avg_ret / std_dev) * (252**0.5) if std_dev > 0 else 0
        return {
            "win_rate": round(win_rate, 3),
            "sharpe": round(sharpe, 3),
            "sample_count": total,
            "avg_return": round(avg_ret, 3),
        }

    async def get_strategy_outcomes(self, days: int = 30) -> list[dict]:
        """전략별 결과(outcome) 조회 - StrategyBandit 피드백용.

        decisions.strategy_scores(JSON)에서 전략 점수를 파싱하고
        decision_outcomes.return_1d로 실현 수익률을 결합합니다.

        Args:
            days: 조회 기간 (기본 30일)

        Returns:
            list[dict]: [
                {
                    "decision_id": int,
                    "ticker": str,
                    "action": str,
                    "return_1d": float,        # 실현 수익률 (소수)
                    "is_correct": bool,
                    "strategy_scores": dict,   # 전략별 원본 점수 (파싱됨)
                }
            ]
        """
        cursor = await self._execute_with_retry(
            """SELECT d.id as decision_id, d.ticker, d.action,
                      d.strategy_scores,
                      o.return_1d, o.is_correct
               FROM decisions d
               JOIN decision_outcomes o ON d.id = o.decision_id
               WHERE d.created_at >= datetime('now', ?)
                 AND o.return_1d IS NOT NULL
               ORDER BY d.created_at DESC""",
            (f"-{days} days",),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            r = dict(row)
            # strategy_scores JSON 파싱
            raw = r.get("strategy_scores") or "{}"
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                parsed = {}
            r["strategy_scores"] = parsed
            result.append(r)
        return result

    # ──────────────────────────────────────────────────
    # trailing_stop_states - 저장 / 로드 (프로세스 재시작 시 복구용)
    # ──────────────────────────────────────────────────
    async def save_trailing_stops(self, states: dict[str, dict]) -> int:
        """DeepAnalyzer.trailing_stops 전체를 DB에 저장 (종료 전 호출).

        Args:
            states: {ticker: state_dict} 형태의 트레일링 스탑 딕셔너리

        Returns:
            저장된 레코드 수
        """
        if not states:
            return 0
        try:
            conn = await self._get_connection()
            count = 0
            for ticker, state in states.items():
                state_json = json.dumps(state, ensure_ascii=False, default=str)
                await conn.execute(
                    """INSERT OR REPLACE INTO trailing_stop_states
                       (ticker, state_json, saved_at)
                       VALUES (?, ?, datetime('now'))""",
                    (ticker, state_json),
                )
                count += 1
            await conn.commit()
            logger.info("✅ trailing_stop_states 저장 완료: %d건", count)
            return count
        except Exception as e:
            logger.error("❌ trailing_stop_states 저장 실패: %s", e)
            return 0

    async def load_trailing_stops(self) -> dict[str, dict]:
        """DB에서 저장된 트레일링 스탑 상태 복구.

        Returns:
            {ticker: state_dict} - 비어있으면 빈 딕셔너리
        """
        try:
            cursor = await self._execute_with_retry(
                "SELECT ticker, state_json FROM trailing_stop_states"
                " ORDER BY saved_at DESC"
            )
            rows = await cursor.fetchall()
            if not rows:
                return {}
            result = {}
            for row in rows:
                try:
                    result[row["ticker"]] = json.loads(row["state_json"])
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("trailing_stop 파싱 실패 (%s): %s", row["ticker"], e)
            logger.info("✅ trailing_stop_states 복구: %d건", len(result))
            return result
        except Exception as e:
            logger.error("❌ trailing_stop_states 로드 실패: %s", e)
            return {}

    async def clear_trailing_stops(self) -> None:
        """저장된 트레일링 스탑 상태 전체 삭제 (정상 종료 후 정리)."""
        try:
            conn = await self._get_connection()
            await conn.execute("DELETE FROM trailing_stop_states")
            await conn.commit()
            logger.debug("trailing_stop_states 초기화 완료")
        except Exception as e:
            logger.warning("trailing_stop_states 초기화 실패: %s", e)

    async def close(self):
        await self._flush_pending()
        if self._pool:
            await self._pool.close()
            self._pool = None
        logger.info("DB 연결 종료 완료")