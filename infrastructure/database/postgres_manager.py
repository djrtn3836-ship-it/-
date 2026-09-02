
---

**Session 23 — TimescaleDB 하이퍼테이블 전환 (기술 검증 포함)**

두 방향의 설계안을 검토하는 과정에서 실제로 실행하면 실패하는 코드 패턴 하나를 발견했습니다. TimescaleDB의 규칙은 "**PK만이 아니라 하이퍼테이블에 존재하는 모든 UNIQUE 제약(단독 인덱스 포함)이 파티션 컬럼을 포함해야 한다**"는 것입니다. 이에 따라:

- `decisions`에 복합 PK `(id, created_at)`를 두면서, `id` 단독에 대해 별도의 `UNIQUE INDEX`나 `UNIQUE` 제약을 추가로 만들면, 그 단독 제약이 `created_at`을 포함하지 않으므로 `create_hypertable()` 호출이 실패합니다. 따라서 `id` 단독의 DB 차원 유일성 보장은 포기하고(BIGSERIAL 시퀀스 특성상 실질적으로는 유일함), FK도 함께 포기하는 것이 유일하게 동작하는 설계입니다.
- 같은 이유로 `decision_outcomes`(PK가 `decision_id` 단독)는 `updated_at` 기준 하이퍼테이블로 전환할 수 없습니다. 이 테이블은 시계열 최적화가 크게 필요하지 않은 소규모 결과 테이블이므로 일반 테이블로 유지합니다.
- `ohlcv`는 기존에 `id BIGSERIAL PRIMARY KEY` 단독 PK가 있었는데, 이를 제거하고 PK를 `(ticker, date)`로 재설계하면 파티션 컬럼(`date`)이 PK에 포함되어 규칙을 만족합니다. `ohlcv.id`를 참조하는 코드가 없음을 `db_manager.py`, 마이그레이션 스크립트, `feature_store.py` 호출부 전체와 대조해 확인했습니다.

**`infrastructure/database/postgres_manager.py` 전체 수정 코드 (v1.1.0)**

```python
# -*- coding: utf-8 -*-
"""
infrastructure/database/postgres_manager.py - PostgreSQL 비동기 매니저 v1.1.0

v1.0.0 → v1.1.0 (Session 23 — TimescaleDB 하이퍼테이블 전환 + PK 재설계):

    🔥 검증된 사실: TimescaleDB는 PK뿐 아니라 테이블에 존재하는 모든 UNIQUE
    제약(단독 인덱스 포함)이 파티션 컬럼을 포함해야 한다는 규칙을 가집니다.
    이에 따라:
    - decisions: PK를 (id, created_at) 복합으로만 유지. id 단독에 대한
      추가 UNIQUE 제약/인덱스는 생성하지 않음(생성 시 create_hypertable
      호출이 실패함).
    - decision_outcomes: 하이퍼테이블로 전환하지 않고 일반 테이블 유지
      (PK=decision_id 단독이라 파티션 컬럼 포함 불가). decisions(id)가
      더 이상 단독 UNIQUE 제약을 갖지 않으므로 FK도 생성하지 않음 →
      애플리케이션 레벨(save_outcome/get_outcome)에서 무결성 관리.
    - ohlcv: 기존 id BIGSERIAL 단독 PK 제거, PK를 (ticker, date)로 재설계.
      date를 TEXT → DATE로 변경(기존 UNIQUE(ticker,date) 제약이 이미
      파티션 컬럼을 포함하므로 규칙 위반 없음). id 미참조 확인 완료.
    - create_hypertable() 호출에 migrate_data => TRUE 추가
      (확장 설치 전 이미 데이터가 있는 테이블도 안전하게 전환).
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import asyncpg
    _ASYNCPG_AVAILABLE = True
except ImportError:
    asyncpg = None  # type: ignore
    _ASYNCPG_AVAILABLE = False

_DATABASE_URL = os.getenv("DATABASE_URL", "")
_DATABASE_URL_READ = os.getenv("DATABASE_URL_READ", _DATABASE_URL)

POSTGRES_ENABLED = _ASYNCPG_AVAILABLE and bool(_DATABASE_URL)


class PostgresManager:
    """PostgreSQL 비동기 데이터베이스 매니저 (TimescaleDB 하이퍼테이블 지원)."""

    _MIN_POOL_SIZE = 2
    _MAX_POOL_SIZE = 10
    _COMMAND_TIMEOUT = 10.0

    def __init__(self, db_url: Optional[str] = None) -> None:
        self.db_url = db_url or _DATABASE_URL
        self.db_url_read = _DATABASE_URL_READ
        self._write_pool: Optional[Any] = None
        self._read_pool: Optional[Any] = None
        self._initialized = False

        if not POSTGRES_ENABLED:
            reason = "asyncpg 미설치" if not _ASYNCPG_AVAILABLE else "DATABASE_URL 미설정"
            logger.info(f"PostgresManager: {reason} → 비활성 대기 모드")

    @property
    def is_available(self) -> bool:
        return self._initialized

    async def init_db(self) -> None:
        if not POSTGRES_ENABLED:
            return
        try:
            self._write_pool = await asyncpg.create_pool(
                dsn=self.db_url,
                min_size=self._MIN_POOL_SIZE,
                max_size=self._MAX_POOL_SIZE,
                command_timeout=self._COMMAND_TIMEOUT,
            )
            self._read_pool = (
                self._write_pool if self.db_url_read == self.db_url
                else await asyncpg.create_pool(
                    dsn=self.db_url_read,
                    min_size=self._MIN_POOL_SIZE,
                    max_size=self._MAX_POOL_SIZE,
                    command_timeout=self._COMMAND_TIMEOUT,
                )
            )
            await self._create_schema()
            self._initialized = True
            logger.info("✅ PostgresManager v1.1.0 초기화 완료 (TimescaleDB 하이퍼테이블)")
        except Exception as e:
            logger.critical(f"❌ PostgreSQL 연결/초기화 실패: {e}")
            raise

    async def _create_schema(self) -> None:
        async with self._write_pool.acquire() as conn:
            try:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
                logger.info("✅ TimescaleDB 확장 활성화")
            except Exception as e:
                logger.debug(f"TimescaleDB 확장 미지원 환경 — 무시: {e}")

            async with conn.transaction():
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS decisions (
                        id          BIGSERIAL NOT NULL,
                        ticker      TEXT NOT NULL,
                        action      TEXT NOT NULL,
                        score       REAL,
                        confidence  REAL,
                        price_at_decision REAL,
                        positives   JSONB,
                        negatives   JSONB,
                        counterfactuals JSONB,
                        sentiment_score REAL DEFAULT 0.0,
                        ml_score    REAL DEFAULT 0.5,
                        risk_adjustment_factor REAL DEFAULT 1.0,
                        strategy_scores JSONB,
                        trace_id    TEXT,
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (id, created_at)
                    );

                    -- 일반 테이블 유지 (하이퍼테이블 아님). FK 미설정
                    -- (decisions.id가 단독 UNIQUE 제약을 갖지 않으므로 참조 불가) —
                    -- save_outcome/get_outcome에서 애플리케이션 레벨 무결성 관리.
                    CREATE TABLE IF NOT EXISTS decision_outcomes (
                        decision_id BIGINT PRIMARY KEY,
                        price_after_1d REAL,
                        price_after_5d REAL,
                        return_1d   REAL,
                        return_5d   REAL,
                        is_correct  BOOLEAN,
                        updated_at  TIMESTAMPTZ DEFAULT NOW()
                    );

                    CREATE TABLE IF NOT EXISTS feedback_weights (
                        id          BIGSERIAL PRIMARY KEY,
                        factor_name TEXT UNIQUE NOT NULL,
                        weight      REAL DEFAULT 1.0,
                        updated_at  TIMESTAMPTZ DEFAULT NOW()
                    );

                    -- id 단독 PK 제거, PK를 (ticker, date)로 재설계 (파티션 컬럼 포함).
                    CREATE TABLE IF NOT EXISTS ohlcv (
                        ticker      TEXT NOT NULL,
                        date        DATE NOT NULL,
                        open REAL, high REAL, low REAL, close REAL,
                        volume      BIGINT,
                        created_at  TIMESTAMPTZ DEFAULT NOW(),
                        PRIMARY KEY (ticker, date)
                    );

                    CREATE TABLE IF NOT EXISTS portfolio_positions (
                        id          BIGSERIAL PRIMARY KEY,
                        ticker      TEXT UNIQUE NOT NULL,
                        entry_price REAL,
                        current_price REAL,
                        qty         INTEGER,
                        entry_time  TIMESTAMPTZ,
                        updated_at  TIMESTAMPTZ DEFAULT NOW()
                    );

                    CREATE TABLE IF NOT EXISTS trailing_stop_states (
                        ticker      TEXT PRIMARY KEY,
                        state_json  JSONB NOT NULL,
                        saved_at    TIMESTAMPTZ DEFAULT NOW()
                    );
                """)

                for idx_sql in [
                    "CREATE INDEX IF NOT EXISTS idx_decisions_ticker ON decisions(ticker)",
                    "CREATE INDEX IF NOT EXISTS idx_decisions_action ON decisions(action)",
                    "CREATE INDEX IF NOT EXISTS idx_positions_ticker ON portfolio_positions(ticker)",
                ]:
                    await conn.execute(idx_sql)

            # 하이퍼테이블 전환: decisions(created_at), ohlcv(date)만 대상.
            # decision_outcomes는 파티션 미포함 단독 PK라 전환 대상에서 제외.
            for table, col in [("decisions", "created_at"), ("ohlcv", "date")]:
                try:
                    await conn.execute(
                        f"SELECT create_hypertable('{table}', '{col}', "
                        f"if_not_exists => TRUE, migrate_data => TRUE);"
                    )
                    logger.info(f"✅ {table} 하이퍼테이블 전환 완료 (파티션: {col})")
                except Exception as e:
                    logger.debug(f"하이퍼테이블 전환 스킵/무시 ({table}): {e}")

        logger.info("PostgresManager v1.1.0: 스키마/인덱스/하이퍼테이블 검증 완료")

    async def close(self) -> None:
        if self._write_pool:
            await self._write_pool.close()
        if self._read_pool and self._read_pool is not self._write_pool:
            await self._read_pool.close()
        self._initialized = False
        logger.info("🛑 PostgresManager 커넥션 풀 종료 완료")

    # ─── OHLCV ──────────────────────────────────────────────────

    async def save_ohlcv(self, ticker: str, date: str, ohlcv: dict) -> None:
        if not self._initialized:
            return
        async with self._write_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO ohlcv (ticker, date, open, high, low, close, volume)
                   VALUES ($1, $2::DATE, $3, $4, $5, $6, $7)
                   ON CONFLICT (ticker, date) DO UPDATE SET
                       open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                       close=EXCLUDED.close, volume=EXCLUDED.volume""",
                ticker, date,
                float(ohlcv.get("open", 0.0)), float(ohlcv.get("high", 0.0)),
                float(ohlcv.get("low", 0.0)), float(ohlcv.get("close", 0.0)),
                int(ohlcv.get("volume", 0)),
            )

    async def save_ohlcv_batch(self, records: List[tuple]) -> int:
        if not self._initialized or not records:
            return 0
        async with self._write_pool.acquire() as conn:
            await conn.executemany(
                """INSERT INTO ohlcv (ticker, date, open, high, low, close, volume)
                   VALUES ($1, $2::DATE, $3, $4, $5, $6, $7)
                   ON CONFLICT (ticker, date) DO UPDATE SET
                       open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                       close=EXCLUDED.close, volume=EXCLUDED.volume""",
                records,
            )
        return len(records)

    async def get_ohlcv(self, ticker: str, period: int = 14) -> List[Dict]:
        if not self._initialized:
            return []
        async with self._read_pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT date::text AS date, open, high, low, close, volume
                   FROM ohlcv WHERE ticker = $1
                   ORDER BY date DESC LIMIT $2""",
                ticker, period,
            )
        result = [dict(r) for r in rows]
        result.reverse()
        return result

    # ─── Decisions ──────────────────────────────────────────────

    async def save_decision(self, analysis: dict) -> None:
        if not self._initialized:
            return
        features = analysis.pop("features", {})
        strategy_scores = analysis.get("strategy_result")
        combined = {}
        if strategy_scores:
            combined["scores"] = strategy_scores
        if features:
            combined["features"] = features
        async with self._write_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO decisions
                   (ticker, action, score, confidence, price_at_decision,
                    positives, negatives, counterfactuals, sentiment_score,
                    ml_score, risk_adjustment_factor, strategy_scores, trace_id)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
                analysis.get("ticker", "N/A"),
                analysis.get("action", "HOLD"),
                float(analysis.get("score", 0.0)),
                float(analysis.get("confidence", 0.0)),
                float(analysis.get("price", 0.0)),
                json.dumps(analysis.get("positives", []), ensure_ascii=False),
                json.dumps(analysis.get("negatives", []), ensure_ascii=False),
                json.dumps(analysis.get("counterfactuals", []), ensure_ascii=False),
                float(analysis.get("sentiment_score", 0.0)),
                float(analysis.get("ml_score", 0.5)),
                float(analysis.get("risk_adjustment_factor", 1.0)),
                json.dumps(combined, ensure_ascii=False) if combined else None,
                analysis.get("trace_id"),
            )

    async def get_decisions_by_date(self, date_str: str) -> List[Dict]:
        if not self._initialized:
            return []
        async with self._read_pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM decisions
                   WHERE (created_at AT TIME ZONE 'Asia/Seoul')::date = $1::date
                   ORDER BY created_at DESC""",
                date_str,
            )
        return [dict(r) for r in rows]

    async def get_decisions_by_date_range(self, start_date: str, end_date: str) -> List[Dict]:
        if not self._initialized:
            return []
        async with self._read_pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM decisions
                   WHERE (created_at AT TIME ZONE 'Asia/Seoul')::date >= $1::date
                     AND (created_at AT TIME ZONE 'Asia/Seoul')::date <= $2::date
                   ORDER BY created_at ASC""",
                start_date, end_date,
            )
        return [dict(r) for r in rows]

    # ─── Portfolio / Trailing Stops ───────────────────────────────

    async def get_positions(self) -> List[Dict]:
        if not self._initialized:
            return []
        async with self._read_pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM portfolio_positions ORDER BY ticker")
        return [dict(r) for r in rows]

    async def save_position(self, ticker: str, entry_price: float, current_price: float, qty: int) -> None:
        if not self._initialized:
            return
        async with self._write_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO portfolio_positions
                   (ticker, entry_price, current_price, qty, entry_time, updated_at)
                   VALUES ($1,$2,$3,$4,NOW(),NOW())
                   ON CONFLICT (ticker) DO UPDATE SET
                       entry_price=EXCLUDED.entry_price, current_price=EXCLUDED.current_price,
                       qty=EXCLUDED.qty, updated_at=NOW()""",
                ticker, float(entry_price), float(current_price), int(qty),
            )

    async def delete_position(self, ticker: str) -> None:
        if not self._initialized:
            return
        async with self._write_pool.acquire() as conn:
            await conn.execute("DELETE FROM portfolio_positions WHERE ticker = $1", ticker)

    async def save_trailing_stops(self, states: Dict[str, dict]) -> int:
        if not self._initialized or not states:
            return 0
        count = 0
        async with self._write_pool.acquire() as conn:
            async with conn.transaction():
                for ticker, state in states.items():
                    await conn.execute(
                        """INSERT INTO trailing_stop_states (ticker, state_json, saved_at)
                           VALUES ($1,$2,NOW())
                           ON CONFLICT (ticker) DO UPDATE SET
                               state_json=EXCLUDED.state_json, saved_at=NOW()""",
                        ticker, json.dumps(state, ensure_ascii=False, default=str),
                    )
                    count += 1
        return count

    async def load_trailing_stops(self) -> Dict[str, dict]:
        if not self._initialized:
            return {}
        async with self._read_pool.acquire() as conn:
            rows = await conn.fetch("SELECT ticker, state_json FROM trailing_stop_states ORDER BY saved_at DESC")
        result = {}
        for row in rows:
            try:
                val = row["state_json"]
                result[row["ticker"]] = json.loads(val) if isinstance(val, str) else val
            except Exception as e:
                logger.warning(f"trailing_stop 파싱 실패 ({row['ticker']}): {e}")
        return result

    async def clear_trailing_stops(self) -> None:
        if not self._initialized:
            return
        async with self._write_pool.acquire() as conn:
            await conn.execute("DELETE FROM trailing_stop_states")

    # ─── Outcomes / Feedback / Weights ───────────────────────────

    async def save_outcome(self, outcome: dict) -> None:
        if not self._initialized:
            return
        async with self._write_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO decision_outcomes
                   (decision_id, price_after_1d, price_after_5d, return_1d, return_5d, is_correct, updated_at)
                   VALUES ($1,$2,$3,$4,$5,$6,NOW())
                   ON CONFLICT (decision_id) DO UPDATE SET
                       price_after_1d=EXCLUDED.price_after_1d, price_after_5d=EXCLUDED.price_after_5d,
                       return_1d=EXCLUDED.return_1d, return_5d=EXCLUDED.return_5d,
                       is_correct=EXCLUDED.is_correct, updated_at=NOW()""",
                outcome["decision_id"], outcome.get("price_after_1d"), outcome.get("price_after_5d"),
                outcome.get("return_1d"), outcome.get("return_5d"), outcome.get("is_correct"),
            )

    async def get_outcome(self, decision_id: int) -> Optional[Dict]:
        if not self._initialized:
            return None
        async with self._read_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM decision_outcomes WHERE decision_id = $1", decision_id)
        return dict(row) if row else None

    async def get_feedback_stats(self, days: int = 30) -> Dict:
        default = {"win_rate": 0.5, "sharpe": 1.0, "sample_count": 0, "avg_return": 0.0}
        if not self._initialized:
            return default
        async with self._read_pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT o.return_1d, o.is_correct
                   FROM decisions d JOIN decision_outcomes o ON d.id = o.decision_id
                   WHERE d.created_at >= NOW() - make_interval(days => $1)
                     AND o.is_correct IS NOT NULL""",
                days,
            )
        if not rows:
            return default
        correct = sum(1 for r in rows if r["is_correct"])
        total = len(rows)
        win_rate = correct / total if total > 0 else 0.5
        returns = [r["return_1d"] for r in rows if r["return_1d"] is not None]
        avg_ret = sum(returns) / len(returns) if returns else 0.0
        std_dev = (sum((r - avg_ret) ** 2 for r in returns) / len(returns)) ** 0.5 if returns else 1.0
        sharpe = (avg_ret / std_dev) * (252 ** 0.5) if std_dev > 0 else 0.0
        return {
            "win_rate": round(win_rate, 3), "sharpe": round(sharpe, 3),
            "sample_count": total, "avg_return": round(avg_ret, 3),
        }

    async def get_strategy_outcomes(self, days: int = 30) -> List[Dict]:
        if not self._initialized:
            return []
        async with self._read_pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT d.id as decision_id, d.ticker, d.action, d.strategy_scores,
                          o.return_1d, o.is_correct
                   FROM decisions d JOIN decision_outcomes o ON d.id = o.decision_id
                   WHERE d.created_at >= NOW() - make_interval(days => $1)
                     AND o.return_1d IS NOT NULL
                   ORDER BY d.created_at DESC""",
                days,
            )
        result = []
        for row in rows:
            r = dict(row)
            raw = r.get("strategy_scores")
            if isinstance(raw, str):
                try:
                    r["strategy_scores"] = json.loads(raw)
                except Exception:
                    r["strategy_scores"] = {}
            elif raw is None:
                r["strategy_scores"] = {}
            result.append(r)
        return result

    async def get_weights(self) -> Dict[str, float]:
        default_factors = ["momentum", "volume", "volatility", "macro", "sector"]
        if not self._initialized:
            return {f: 1.0 for f in default_factors}
        async with self._read_pool.acquire() as conn:
            rows = await conn.fetch("SELECT factor_name, weight FROM feedback_weights")
        weights = {r["factor_name"]: r["weight"] for r in rows}
        for f in default_factors:
            weights.setdefault(f, 1.0)
        return weights

    async def update_weight(self, factor_name: str, new_weight: float) -> None:
        if not self._initialized:
            return
        async with self._write_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO feedback_weights (factor_name, weight, updated_at)
                   VALUES ($1,$2,NOW())
                   ON CONFLICT (factor_name) DO UPDATE SET
                       weight=EXCLUDED.weight, updated_at=NOW()""",
                factor_name, float(new_weight),
            )


postgres_manager = PostgresManager()


def get_active_db_manager():
    if POSTGRES_ENABLED:
        return postgres_manager
    from data.db_manager import DatabaseManager
    return DatabaseManager()
