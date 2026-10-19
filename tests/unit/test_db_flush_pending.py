# -*- coding: utf-8 -*-
"""DB 잔여 트랜잭션 락 실버그 회귀 테스트 (S137).

실측 사고(2026-10-19 18:01): 배치 커밋이 '다음 쓰기'에서만 트리거되는 구조라
대량 쓰기(학습 41,235건) 후 유휴 시 미커밋 트랜잭션이 남아 **외부 쓰기 전면 차단**.
수정: flush_pending() 공개 API + 메인 루프 10초 주기 + 학습기 완료 시 커밋.
"""
import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data.db_manager import DatabaseManager  # noqa: E402


def _external_can_write(db_path: Path, wait: float = 2.0) -> bool:
    """외부(다른 연결) 쓰기 락 획득 가능 여부."""
    con = sqlite3.connect(str(db_path), timeout=wait)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


class TestFlushPending:

    def test_pending_write_blocks_then_flush_releases(self, tmp_path):
        db_path = tmp_path / "t.db"

        async def flow() -> bool:
            db = DatabaseManager(db_path=db_path)
            await db.init_db()
            # 배치 커밋 임계(20) 미만으로 써서 '잔여 트랜잭션' 상태를 만든다
            await db.save_outcome({
                "decision_id": 1, "price_after_1d": 100.0, "price_after_5d": None,
                "return_1d": 1.0, "return_5d": None, "is_correct": True,
            })
            blocked = not _external_can_write(db_path)
            await db.flush_pending()
            released = _external_can_write(db_path)
            await db.close()
            return blocked and released

        assert asyncio.run(flow()) is True, "미커밋 시 차단 → flush 후 해제되어야 함"

    def test_flush_is_noop_when_clean(self, tmp_path):
        async def flow() -> None:
            db = DatabaseManager(db_path=tmp_path / "e.db")
            await db.init_db()
            await db.flush_pending()  # 잔여 없음 → 예외 없음
            await db.close()

        asyncio.run(flow())


class TestWiring:

    def test_main_loop_flushes(self):
        src = (Path(__file__).resolve().parents[2] / "app" / "bootstrap.py").read_text(encoding="utf-8")
        assert "flush_pending" in src and "_last_db_flush_ts" in src

    def test_learner_flushes_after_bulk(self):
        src = (Path(__file__).resolve().parents[2] / "feedback" / "feedback_learner.py").read_text(encoding="utf-8")
        assert "await self.db.flush_pending()" in src
