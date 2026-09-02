#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/migrate_sqlite_to_postgres.py - v1.0.0 (Session 22)

SQLite(data/decisions.db) → PostgreSQL 데이터 마이그레이션 스크립트.
⚠️ 실제 운영 데이터로 검증되지 않은 초안입니다. --dry-run으로 건수를 먼저
   확인하고, 가능하면 백업 복제본에 먼저 실행해 보시기를 권장합니다.

전제 조건:
    1. docker-compose up -d 로 PostgreSQL 기동
    2. .env에 DATABASE_URL 설정
    3. pip install asyncpg (requirements.txt에 이미 포함됨)

사용법:
    python scripts/migrate_sqlite_to_postgres.py --dry-run
    python scripts/migrate_sqlite_to_postgres.py

설계 원칙:
    - 모든 INSERT는 ON CONFLICT로 idempotent 처리 → 중단 후 재실행 안전
    - decisions.id는 decision_outcomes.decision_id의 FK 대상이므로 원본
      SQLite id를 보존하여 INSERT, 이후 BIGSERIAL 시퀀스를 setval()로 동기화
    - JSON 컬럼은 SQLite에 이미 json.dumps()된 TEXT로 저장되어 있어 재직렬화
      없이 그대로 전달 (asyncpg는 사전 직렬화된 JSON 문자열을 jsonb에 직접 전달 가능)
    - ⚠️ 타임스탬프 가정: SQLite의 CURRENT_TIMESTAMP는 기본적으로 UTC를
      반환합니다. 이 값을 UTC로 간주해 TIMESTAMPTZ로 이전합니다(임의 보정 없음).
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import aiosqlite
from dotenv import load_dotenv

from core.logger import setup_logger

logger = setup_logger("migrate")

SQLITE_DB_PATH = Path(__file__).parent.parent / "data" / "decisions.db"
DEFAULT_BATCH_SIZE = 500


def _parse_sqlite_ts(value: Any) -> Optional[datetime]:
    """SQLite TEXT 타임스탬프를 UTC datetime으로 변환. 실패 시 None(NULL 처리)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning(f"타임스탬프 파싱 실패, NULL 처리: {value!r}")
        return None


async def _fetch_all(sconn: aiosqlite.Connection, table: str) -> List[Dict[str, Any]]:
    sconn.row_factory = aiosqlite.Row
    cursor = await sconn.execute(f"SELECT * FROM {table}")
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def migrate_decisions(sconn: Any, pool: Any, dry_run: bool) -> int:
    rows = await _fetch_all(sconn, "decisions")
    if dry_run or not rows:
        return len(rows)
    async with pool.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                await conn.execute(
                    """INSERT INTO decisions
                       (id, ticker, action, score, confidence, price_at_decision,
                        positives, negatives, counterfactuals, sentiment_score,
                        ml_score, risk_adjustment_factor, strategy_scores,
                        trace_id, created_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                       ON CONFLICT (id) DO NOTHING""",
                    r["id"], r["ticker"], r["action"], r["score"], r["confidence"],
                    r["price_at_decision"], r["positives"], r["negatives"],
                    r["counterfactuals"], r["sentiment_score"], r["ml_score"],
                    r["risk_adjustment_factor"], r["strategy_scores"],
                    r["trace_id"], _parse_sqlite_ts(r["created_at"]),
                )
            max_id = max((r["id"] for r in rows), default=0)
            if max_id:
                await conn.execute(
                    "SELECT setval(pg_get_serial_sequence('decisions','id'), $1, true)",
                    max_id,
                )
    return len(rows)


async def migrate_decision_outcomes(sconn: Any, pool: Any, dry_run: bool) -> int:
    rows = await _fetch_all(sconn, "decision_outcomes")
    if dry_run or not rows:
        return len(rows)
    async with pool.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                await conn.execute(
                    """INSERT INTO decision_outcomes
                       (decision_id, price_after_1d, price_after_5d,
                        return_1d, return_5d, is_correct, updated_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7)
                       ON CONFLICT (decision_id) DO NOTHING""",
                    r["decision_id"], r["price_after_1d"], r["price_after_5d"],
                    r["return_1d"], r["return_5d"], r["is_correct"],
                    _parse_sqlite_ts(r["updated_at"]),
                )
    return len(rows)


async def migrate_feedback_weights(sconn: Any, pool: Any, dry_run: bool) -> int:
    rows = await _fetch_all(sconn, "feedback_weights")
    if dry_run or not rows:
        return len(rows)
    async with pool.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                await conn.execute(
                    """INSERT INTO feedback_weights (factor_name, weight, updated_at)
                       VALUES ($1,$2,$3)
                       ON CONFLICT (factor_name) DO UPDATE SET
                           weight=EXCLUDED.weight, updated_at=EXCLUDED.updated_at""",
                    r["factor_name"], r["weight"], _parse_sqlite_ts(r["updated_at"]),
                )
    return len(rows)


async def migrate_ohlcv(sconn: Any, pool: Any, dry_run: bool, batch_size: int) -> int:
    rows = await _fetch_all(sconn, "ohlcv")
    if dry_run or not rows:
        return len(rows)
    async with pool.acquire() as conn:
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            async with conn.transaction():
                await conn.executemany(
                    """INSERT INTO ohlcv (ticker, date, open, high, low, close, volume)
                       VALUES ($1, $2::DATE, $3, $4, $5, $6, $7)
                       ON CONFLICT (ticker, date) DO NOTHING""",
                    [(r["ticker"], r["date"], r["open"], r["high"],
                      r["low"], r["close"], r["volume"]) for r in batch],
                )
            logger.info(f"ohlcv 진행: {min(i + batch_size, len(rows))}/{len(rows)}")
    return len(rows)



async def migrate_portfolio_positions(sconn: Any, pool: Any, dry_run: bool) -> int:
    rows = await _fetch_all(sconn, "portfolio_positions")
    if dry_run or not rows:
        return len(rows)
    async with pool.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                await conn.execute(
                    """INSERT INTO portfolio_positions
                       (ticker, entry_price, current_price, qty, entry_time, updated_at)
                       VALUES ($1,$2,$3,$4,$5,$6)
                       ON CONFLICT (ticker) DO UPDATE SET
                           entry_price=EXCLUDED.entry_price,
                           current_price=EXCLUDED.current_price,
                           qty=EXCLUDED.qty, updated_at=EXCLUDED.updated_at""",
                    r["ticker"], r["entry_price"], r["current_price"], r["qty"],
                    _parse_sqlite_ts(r["entry_time"]), _parse_sqlite_ts(r["updated_at"]),
                )
    return len(rows)


async def migrate_trailing_stop_states(sconn: Any, pool: Any, dry_run: bool) -> int:
    rows = await _fetch_all(sconn, "trailing_stop_states")
    if dry_run or not rows:
        return len(rows)
    async with pool.acquire() as conn:
        async with conn.transaction():
            for r in rows:
                await conn.execute(
                    """INSERT INTO trailing_stop_states (ticker, state_json, saved_at)
                       VALUES ($1,$2,$3)
                       ON CONFLICT (ticker) DO UPDATE SET
                           state_json=EXCLUDED.state_json, saved_at=EXCLUDED.saved_at""",
                    r["ticker"], r["state_json"], _parse_sqlite_ts(r["saved_at"]),
                )
    return len(rows)


async def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 마이그레이션")
    parser.add_argument("--dry-run", action="store_true", help="실제 이전 없이 건수만 확인")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()

    try:
        import asyncpg  # noqa: F401
    except ImportError:
        print("❌ asyncpg가 설치되어 있지 않습니다: pip install asyncpg")
        sys.exit(1)

    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        print("❌ .env에 DATABASE_URL이 설정되어 있지 않습니다.")
        sys.exit(1)
    if not SQLITE_DB_PATH.exists():
        print(f"❌ SQLite DB를 찾을 수 없습니다: {SQLITE_DB_PATH}")
        sys.exit(1)

    from infrastructure.database.postgres_manager import PostgresManager
    pg = PostgresManager(db_url=database_url)

    label = "[DRY-RUN] " if args.dry_run else ""
    print(f"{label}마이그레이션 시작: {SQLITE_DB_PATH} → {database_url.split('@')[-1]}")

    async with aiosqlite.connect(SQLITE_DB_PATH) as sconn:
        pool = None
        if not args.dry_run:
            await pg.init_db()  # 스키마 보장 (이미 존재하면 무해)
            pool = pg._write_pool  # 내부 풀 직접 재사용 (마이그레이션 스크립트 한정 예외)

        results: Dict[str, int] = {}
        results["decisions"] = await migrate_decisions(sconn, pool, args.dry_run)
        results["decision_outcomes"] = await migrate_decision_outcomes(sconn, pool, args.dry_run)
        results["feedback_weights"] = await migrate_feedback_weights(sconn, pool, args.dry_run)
        results["ohlcv"] = await migrate_ohlcv(sconn, pool, args.dry_run, args.batch_size)
        results["portfolio_positions"] = await migrate_portfolio_positions(sconn, pool, args.dry_run)
        results["trailing_stop_states"] = await migrate_trailing_stop_states(sconn, pool, args.dry_run)

        if not args.dry_run:
            await pg.close()

    print("\n" + "=" * 50)
    print(f"{label}마이그레이션 결과:")
    for table, count in results.items():
        print(f"  {table}: {count}건")
    print("=" * 50)
    if args.dry_run:
        print("\n실제 이전을 실행하려면 --dry-run 옵션 없이 다시 실행하세요.")
    else:
        print("\n✅ 완료. Postgres에서 SELECT COUNT(*)로 각 테이블 건수를 직접 대조해 검증하세요.")


if __name__ == "__main__":
    asyncio.run(main())
