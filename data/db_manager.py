# -*- coding: utf-8 -*-
"""
data/db_manager.py - v7.0.1 (Session 31: mypy 타입 보강)

v7.0.0 → v7.0.1 변경 사항:
    - 모든 async 메서드에 반환 타입 명시 (-> None, -> list[dict[str, Any]] 등)
    - tuple/dict/list 제네릭 타입 인자 전면 명시
    - _execute_batched()의 params 타입을 tuple[Any, ...] | dict[str, Any]로 확장
      (save_outcome()이 named placeholder(:key) 바인딩을 위해 dict를 전달하는데,
       기존에는 params가 tuple로만 선언되어 있어 실제 타입 불일치가 있었음.
       aiosqlite는 dict/tuple 둘 다 허용하므로 동작 자체는 항상 정상이었음)
    - _execute_read()의 last_error를 Exception | None으로 명시하고,
      루프 종료 후 raise를 None-safe하게 정리 (raise None 방지)
    - asyncio.wait_for()는 원본 그대로 보존 (해당 위치에 실제 오류 없음 확인)
    - 로직/동작 100% 무변경 — 타입 힌트만 추가
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from core.debug_tower import debug_tower
from core.logger import setup_logger
from observability.trace_propagation import inject_trace_id

logger = setup_logger("db_manager")
DB_PATH = Path(__file__).parent.parent / "data" / "decisions.db"

MAX_CONNECTIONS = 5
CONNECTION_TIMEOUT = 10
QUERY_TIMEOUT = 5.0
MAX_RETRIES = 2


class DatabaseManager:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        if isinstance(db_path, str):
            db_path = Path(db_path)
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._write_conn: aiosqlite.Connection | None = None
        self._read_conn: aiosqlite.Connection | None = None

        self._last_analyze = datetime.min
        self._analyze_interval = timedelta(days=1)

        self._pending_writes = 0
        self._batch_size = 20
        self._commit_interval = 0.5
        self._last_commit_time = time.time()

    async def _get_write_conn(self) -> aiosqlite.Connection:
        """쓰기 전용 커넥션 반환 (WAL 모드)."""
        if self._write_conn is None:
            try:
                self._write_conn = await asyncio.wait_for(
                    aiosqlite.connect(self.db_path, timeout=CONNECTION_TIMEOUT), timeout=CONNECTION_TIMEOUT
                )
                self._write_conn.row_factory = aiosqlite.Row
                await self._write_conn.execute("PRAGMA journal_mode=WAL")
                await self._write_conn.execute("PRAGMA synchronous=NORMAL")
                await self._write_conn.execute("PRAGMA cache_size=-20000")
                logger.info("DB 쓰기 커넥션 초기화 완료 (WAL 모드)")
            except TimeoutError:
                logger.error("DB 쓰기 커넥션 타임아웃")
                raise
        return self._write_conn

    async def _get_read_conn(self) -> aiosqlite.Connection:
        """읽기 전용 커넥션 반환 (mode=ro). 실패 시 일반 연결로 자동 폴백."""
        if self._read_conn is None:
            try:
                db_uri = f"{self.db_path.absolute().as_uri()}?mode=ro"
                self._read_conn = await asyncio.wait_for(
                    aiosqlite.connect(db_uri, uri=True, timeout=CONNECTION_TIMEOUT), timeout=CONNECTION_TIMEOUT
                )
                self._read_conn.row_factory = aiosqlite.Row
                logger.info("DB 읽기 커넥션 초기화 완료 (Read-Only)")
            except aiosqlite.OperationalError as e:
                logger.warning(f"mode=ro 연결 실패({e}), 일반 연결로 읽기 커넥션 폴백")
                self._read_conn = await asyncio.wait_for(
                    aiosqlite.connect(self.db_path, timeout=CONNECTION_TIMEOUT), timeout=CONNECTION_TIMEOUT
                )
                self._read_conn.row_factory = aiosqlite.Row
            except TimeoutError:
                logger.error("DB 읽기 커넥션 타임아웃")
                raise
        return self._read_conn

    async def _execute_read(
        self,
        query: str,
        params: tuple[Any, ...] = (),
        retries: int = MAX_RETRIES,
    ) -> list[dict[str, Any]]:
        """읽기 전용 쿼리 실행 (원본 재시도 로직 100% 보존 + dict 리스트 자동 변환)."""
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                conn = await self._get_read_conn()
                async with asyncio.timeout(QUERY_TIMEOUT):
                    cursor = await conn.execute(query, params)
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
            except (TimeoutError, aiosqlite.OperationalError) as e:
                last_error = e
                if "database is locked" in str(e) and attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise
            except Exception:
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("_execute_read: 도달 불가 경로 (재시도 루프 예외 없이 종료)")

    async def _execute_batched(
        self,
        query: str,
        params: tuple[Any, ...] | dict[str, Any] = (),
    ) -> None:
        """쓰기 전용 배치 쿼리 실행.

        params는 위치 기반(tuple) 및 named placeholder(:key) 기반(dict) 바인딩을
        모두 지원합니다 — save_outcome()이 dict를 전달하는 실제 사용 패턴을 정확히 반영.
        """
        conn = await self._get_write_conn()
        await conn.execute(query, params)
        self._pending_writes += 1

        now = time.time()
        if self._pending_writes >= self._batch_size or (now - self._last_commit_time) >= self._commit_interval:
            await conn.commit()
            self._pending_writes = 0
            self._last_commit_time = now

    async def _flush_pending(self) -> None:
        if self._pending_writes > 0:
            conn = await self._get_write_conn()
            await conn.commit()
            self._pending_writes = 0
            logger.debug("배치 커밋 플러시 완료")

    async def init_db(self) -> None:
        conn = await self._get_write_conn()

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
                trace_id TEXT DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        try:
            await conn.execute("ALTER TABLE decisions ADD COLUMN trace_id TEXT DEFAULT NULL")
            await conn.commit()
            logger.info("decisions 테이블에 trace_id 컬럼 추가")
        except Exception:
            pass

        await conn.executescript("""
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

        logger.info("DB 초기화 완료 (v7.0.1 - CQRS + 타입 보강)")
        debug_tower.log("SYSTEM", "DB_INIT_DONE", {})

    async def analyze_db(self) -> None:
        now = datetime.now()
        if (now - self._last_analyze) < self._analyze_interval:
            return
        try:
            conn = await self._get_write_conn()
            await conn.execute("ANALYZE")
            await conn.commit()
            self._last_analyze = now
            logger.debug("DB ANALYZE 완료")
        except Exception as e:
            logger.debug(f"ANALYZE 실패: {e}")

    async def save_ohlcv(self, ticker: str, date: str, ohlcv: dict[str, Any]) -> None:
        debug_tower.log(ticker, "DB_SAVE_OHLCV", {"date": date})
        await self._execute_batched(
            """INSERT OR REPLACE INTO ohlcv (ticker, date, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticker, date, ohlcv.get("open", 0.0), ohlcv.get("high", 0.0),
             ohlcv.get("low", 0.0), ohlcv.get("close", 0.0), ohlcv.get("volume", 0)),
        )

    async def save_ohlcv_batch(self, records: list[tuple[Any, ...]]) -> int:
        if not records:
            return 0
        conn = await self._get_write_conn()
        await conn.executemany(
            """INSERT OR REPLACE INTO ohlcv (ticker, date, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            records,
        )
        await conn.commit()
        logger.debug(f"OHLCV 배치 저장 완료: {len(records)}개 레코드")
        return len(records)

    async def get_ohlcv(self, ticker: str, period: int = 14) -> list[dict[str, Any]]:
        debug_tower.log(ticker, "DB_GET_OHLCV", {"period": period})
        result = await self._execute_read(
            "SELECT date, open, high, low, close, volume FROM ohlcv WHERE ticker = ? ORDER BY date DESC LIMIT ?",
            (ticker, period),
        )
        result.reverse()
        return result

    async def get_ohlcv_range(self, ticker: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return await self._execute_read(
            """SELECT date, open, high, low, close, volume
               FROM ohlcv
               WHERE ticker = ? AND date >= ? AND date <= ?
               ORDER BY date ASC""",
            (ticker, start_date, end_date),
        )

    async def save_decision(self, analysis: dict[str, Any]) -> None:
        inject_trace_id(analysis, key="trace_id")
        features = analysis.pop("features", {})
        strategy_scores = analysis.get("strategy_result")
        combined_json: dict[str, Any] = {}
        if strategy_scores:
            combined_json["scores"] = strategy_scores
        if features:
            combined_json["features"] = features
        strategy_json = json.dumps(combined_json, ensure_ascii=False) if combined_json else None

        ticker = analysis.get("ticker", "UNKNOWN")
        debug_tower.log(ticker, "DB_SAVE_DECISION", {"action": analysis.get("action")})

        await self._execute_batched(
            """INSERT INTO decisions
               (ticker, action, score, confidence, price_at_decision, positives, negatives, counterfactuals,
                sentiment_score, ml_score, risk_adjustment_factor, strategy_scores, trace_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                analysis.get("ticker", "N/A"), analysis.get("action", "HOLD"),
                analysis.get("score", 0.0), analysis.get("confidence", 0.0),
                analysis.get("price", 0.0),
                json.dumps(analysis.get("positives", []), ensure_ascii=False),
                json.dumps(analysis.get("negatives", []), ensure_ascii=False),
                json.dumps(analysis.get("counterfactuals", []), ensure_ascii=False),
                analysis.get("sentiment_score", 0.0), analysis.get("ml_score", 0.5),
                analysis.get("risk_adjustment_factor", 1.0), strategy_json,
                analysis.get("trace_id"),
            ),
        )

    async def get_decisions_by_date(self, date_str: str) -> list[dict[str, Any]]:
        return await self._execute_read(
            "SELECT * FROM decisions WHERE DATE(created_at) = ? ORDER BY created_at DESC", (date_str,)
        )

    async def get_decisions_by_date_range(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return await self._execute_read(
            """SELECT * FROM decisions
               WHERE DATE(created_at) >= ? AND DATE(created_at) <= ?
               ORDER BY created_at ASC""",
            (start_date, end_date),
        )

    async def save_position(self, ticker: str, entry_price: float, current_price: float, qty: int) -> None:
        await self._execute_batched(
            """INSERT OR REPLACE INTO portfolio_positions (ticker, entry_price, current_price, qty, entry_time, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (ticker, entry_price, current_price, qty),
        )

    async def delete_position(self, ticker: str) -> None:
        await self._execute_batched("DELETE FROM portfolio_positions WHERE ticker = ?", (ticker,))

    async def get_positions(self) -> list[dict[str, Any]]:
        return await self._execute_read("SELECT * FROM portfolio_positions ORDER BY ticker")

    async def get_weights(self) -> dict[str, float]:
        rows = await self._execute_read("SELECT factor_name, weight FROM feedback_weights")
        weights: dict[str, float] = {row["factor_name"]: row["weight"] for row in rows}
        default_factors = ["momentum", "volume", "volatility", "macro", "sector"]
        for f in default_factors:
            if f not in weights:
                weights[f] = 1.0
        return weights

    async def update_weight(self, factor_name: str, new_weight: float) -> None:
        await self._execute_batched(
            """INSERT OR REPLACE INTO feedback_weights (factor_name, weight, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)""",
            (factor_name, new_weight),
        )

    async def save_outcome(self, outcome: dict[str, Any]) -> None:
        await self._execute_batched(
            """INSERT OR REPLACE INTO decision_outcomes
               (decision_id, price_after_1d, price_after_5d, return_1d, return_5d, is_correct)
               VALUES (:decision_id, :price_after_1d, :price_after_5d, :return_1d, :return_5d, :is_correct)""",
            outcome,
        )

    async def get_outcome(self, decision_id: int) -> dict[str, Any] | None:
        rows = await self._execute_read(
            "SELECT * FROM decision_outcomes WHERE decision_id = ?", (decision_id,)
        )
        return rows[0] if rows else None

    async def get_feedback_stats(self, days: int = 30) -> dict[str, Any]:
        rows = await self._execute_read(
            """SELECT d.action, o.return_1d, o.is_correct
               FROM decisions d
               JOIN decision_outcomes o ON d.id = o.decision_id
               WHERE d.created_at >= datetime('now', ?)
               AND o.is_correct IS NOT NULL""",
            (f"-{days} days",),
        )
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
            "win_rate": round(win_rate, 3), "sharpe": round(sharpe, 3),
            "sample_count": total, "avg_return": round(avg_ret, 3),
        }

    async def get_strategy_outcomes(self, days: int = 30) -> list[dict[str, Any]]:
        rows = await self._execute_read(
            """SELECT d.id as decision_id, d.ticker, d.action, d.strategy_scores,
                      o.return_1d, o.is_correct
               FROM decisions d
               JOIN decision_outcomes o ON d.id = o.decision_id
               WHERE d.created_at >= datetime('now', ?)
                 AND o.return_1d IS NOT NULL
               ORDER BY d.created_at DESC""",
            (f"-{days} days",),
        )
        result: list[dict[str, Any]] = []
        for r in rows:
            raw = r.get("strategy_scores") or "{}"
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                parsed = {}
            r["strategy_scores"] = parsed
            result.append(r)
        return result

    async def save_trailing_stops(self, states: dict[str, dict[str, Any]]) -> int:
        if not states:
            return 0
        try:
            conn = await self._get_write_conn()
            count = 0
            for ticker, state in states.items():
                state_json = json.dumps(state, ensure_ascii=False, default=str)
                await conn.execute(
                    """INSERT OR REPLACE INTO trailing_stop_states (ticker, state_json, saved_at)
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

    async def load_trailing_stops(self) -> dict[str, dict[str, Any]]:
        try:
            rows = await self._execute_read(
                "SELECT ticker, state_json FROM trailing_stop_states ORDER BY saved_at DESC"
            )
            if not rows:
                return {}
            result: dict[str, dict[str, Any]] = {}
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
        try:
            conn = await self._get_write_conn()
            await conn.execute("DELETE FROM trailing_stop_states")
            await conn.commit()
            logger.debug("trailing_stop_states 초기화 완료")
        except Exception as e:
            logger.warning("trailing_stop_states 초기화 실패: %s", e)

    async def close(self) -> None:
        await self._flush_pending()
        if self._write_conn:
            await self._write_conn.close()
            self._write_conn = None
        if self._read_conn:
            await self._read_conn.close()
            self._read_conn = None
        logger.info("DB 연결 종료 완료")
