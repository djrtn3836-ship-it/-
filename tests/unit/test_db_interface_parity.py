# -*- coding: utf-8 -*-
"""tests/unit/test_db_interface_parity.py - PostgreSQL 인터페이스 정합성 (P6-3).

배경
----
`DATABASE_URL`을 설정하면 `PostgresManager`가 활성화되는데, 이 클래스는
`DatabaseManager`(SQLite)의 공개 메서드를 전부 구현하지 않았다.
미구현 메서드를 호출하면 런타임 `AttributeError`로 **프로덕션 경로가 깨진다**
(예: `get_daily_total_volume` → 서킷브레이커, `momentum_paper` 4종 → 모멘텀 리포트).

이 테스트의 목적
----------------
1. 미구현 목록이 **문서화된 집합과 정확히 일치**하는지 검증한다.
   → 새 메서드를 SQLite에만 추가하면 CI가 즉시 실패한다(무음 격차 방지).
2. 미구현 메서드를 실제로 채우면(=집합이 줄면) 이 테스트도 실패한다.
   → 문서/결정을 갱신하도록 강제한다.
"""

from data.db_manager import DatabaseManager

# 2026-10-01 기준 알려진 미구현 목록 (docs/postgres_migration_assessment.md 참조)
KNOWN_GAPS = {
    "analyze_db",
    "get_daily_total_volume",
    "get_latest_momentum_paper_return",
    "get_momentum_paper_pending",
    "get_momentum_paper_stats",
    "get_ohlcv_range",
    "save_momentum_picks",
    "update_momentum_paper_result",
}

# 전환 시 즉시 깨지는 프로덕션 경로 (회귀 방지용 명시)
PRODUCTION_CRITICAL_GAPS = {
    "get_daily_total_volume",      # risk/market_risk_monitor.py (서킷브레이커 거래량 입력)
    "get_ohlcv_range",             # scheduler/momentum_report.py, validation/backtest_runner.py
    "save_momentum_picks",         # scheduler/momentum_report.py (모의 추적 기록)
    "get_momentum_paper_pending",  # scheduler/momentum_report.py (평가 대상 조회)
}


class TestPostgresInterfaceParity:
    def test_gap_set_matches_documented(self) -> None:
        actual = set(DatabaseManager.missing_in_postgres())
        assert actual == KNOWN_GAPS, (
            "PostgreSQL 미구현 메서드 집합이 문서와 불일치합니다. "
            f"신규 누락: {sorted(actual - KNOWN_GAPS)} / "
            f"해소됨(문서 갱신 필요): {sorted(KNOWN_GAPS - actual)}"
        )

    def test_production_critical_gaps_documented(self) -> None:
        actual = set(DatabaseManager.missing_in_postgres())
        missing_docs = PRODUCTION_CRITICAL_GAPS - actual
        assert missing_docs == set(), (
            f"프로덕션 필수 메서드가 PG에 구현됨 → 문서/테스트 갱신 필요: {sorted(missing_docs)}"
        )

    def test_contract_is_substantial(self) -> None:
        """계약 집합이 실제로 존재하는지(빈 집합으로 인한 공허한 통과 방지)."""
        assert len(DatabaseManager.public_method_names()) >= 20

    def test_missing_in_postgres_handles_absent_module(self, monkeypatch) -> None:
        class _Empty:
            pass

        gaps = DatabaseManager.missing_in_postgres(_Empty)
        assert set(gaps) == DatabaseManager.public_method_names()

    def test_known_sqlite_methods_are_part_of_contract(self) -> None:
        """최근 추가된 모멘텀/리스크 메서드가 계약에 포함되어 있는지 확인."""
        contract = DatabaseManager.public_method_names()
        for name in ("save_ohlcv", "save_decision", "get_ohlcv_range", "save_momentum_picks"):
            assert name in contract, f"{name}이 계약에서 누락"
