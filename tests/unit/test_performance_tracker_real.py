# -*- coding: utf-8 -*-
"""
tests/unit/test_performance_tracker_real.py - 성과추적기 실측 검증

배경:
    PerformanceTracker v2.0의 `_get_daily_returns()`가 "실제 DB outcome 기반"으로
    동작한다고 문서화되어 있으나, 실측 검증 이력이 없었다.
    본 테스트는 임시 SQLite DB에 decision + decision_outcome을 실제로 저장한 뒤
    `_update_metrics()`가 0/스텁이 아닌 실수치를 산출하는지 확인한다.

    (운영 DB `data/decisions.db`는 현재 스키마만 있고 행이 0건이므로,
     실측 검증은 임시 DB 시딩으로 수행한다.)
"""

import pytest

from analytics.performance_tracker import PerformanceTracker
from data.db_manager import DatabaseManager


def _reset(pt: PerformanceTracker) -> None:
    """싱글톤 상태 초기화 (테스트 간 상태 오염 방지)."""
    pt._equity_curve = [100.0]
    pt._daily_returns = []
    pt._snapshots = []
    pt._daily_pnl_cache = []


async def _seed(db: DatabaseManager, n: int, ret: float) -> None:
    """decision n건 + 대응 outcome을 저장하고 커밋한다."""
    for i in range(n):
        await db.save_decision(
            {
                "ticker": f"00000{i}",
                "action": "BUY",
                "score": 0.8,
                "confidence": 0.8,
                "price": 10000.0,
            }
        )
    await db._flush_pending()

    decisions = await db.get_decisions_by_date_range("2000-01-01", "2999-12-31")
    assert len(decisions) == n

    for d in decisions:
        await db.save_outcome(
            {
                "decision_id": d["id"],
                "price_after_1d": 10000.0 * (1 + ret),
                "price_after_5d": 10000.0 * (1 + ret),
                "return_1d": ret,
                "return_5d": ret,
                "is_correct": 1,
            }
        )
    await db._flush_pending()


async def test_performance_tracker_computes_real_values(tmp_path) -> None:
    db = DatabaseManager(db_path=tmp_path / "perf.db")
    await db.init_db()
    await _seed(db, n=6, ret=0.01)

    pt = PerformanceTracker()
    _reset(pt)
    pt.initialize(db)
    await pt._update_metrics()

    snap = pt.get_latest_snapshot()
    assert snap is not None
    assert snap.total_trades == 6                      # 실집계(스텁 0 아님)
    assert snap.win_rate == pytest.approx(1.0)         # is_correct=1 × 6
    assert snap.equity != 100.0                        # 수익률이 반영됨
    assert snap.daily_pnl_pct == pytest.approx(1.0)    # 0.01 → 1.0%
    assert snap.total_pnl_pct == pytest.approx(1.0)    # equity 101.0 - 100

    await db.close()


async def test_performance_tracker_empty_db_is_safe(tmp_path) -> None:
    """데이터가 없어도 크래시하지 않고 0 기준값을 유지해야 한다."""
    db = DatabaseManager(db_path=tmp_path / "empty.db")
    await db.init_db()

    pt = PerformanceTracker()
    _reset(pt)
    pt.initialize(db)
    await pt._update_metrics()

    snap = pt.get_latest_snapshot()
    assert snap is not None
    assert snap.total_trades == 0
    assert snap.equity == pytest.approx(100.0)

    await db.close()
