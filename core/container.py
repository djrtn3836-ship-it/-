"""
core/container.py - v1.3 (Phase 4: PostgreSQL 스위칭 + 자동 폴백)

v1.2 → v1.3 변경 사항 (교차검증으로 발견한 문제 반영):
    - db_manager 프로퍼티가 infrastructure/database/postgres_manager.py의
      get_active_db_manager()를 재사용하도록 단순화했습니다. POSTGRES_ENABLED
      판정 로직(asyncpg 설치 여부 + DATABASE_URL 존재 여부)을 이곳에 다시
      구현하지 않아, 두 판정 로직이 서로 어긋나는 것을 원천적으로 방지합니다.
    - 🔥 CRITICAL: initialize()의 performance_tracker.initialize(self.db_manager)
      호출을 절대 제거하지 않고 보존합니다.
      검증: app/bootstrap.py의 start_performance_tracker() 독스트링에
      "performance_tracker.initialize(db)는 container.initialize() 내부에서
      이미 호출되었으므로 재호출하지 않습니다"라고 명시되어 있습니다. 이 호출을
      제거하면 PerformanceTracker._initialized가 False로 남아 start()가
      `if self._running or not self._initialized: return`에 걸려 조용히
      no-op되고, 성과 추적 백그라운드 루프가 영원히 시작되지 않는 심각한
      회귀가 발생합니다. (검토 과정에서 이 회귀를 실제로 만드는 초안을
      발견하여 이번 버전에서 명시적으로 방지합니다.)
    - 🆕 initialize()에 PostgreSQL 연결 실패 시 SQLite 자동 폴백을 추가했습니다.
      DATABASE_URL은 설정되어 있지만 실제 서버가 응답하지 않는 경우
      (Docker 미기동, 방화벽 등) 시스템 전체가 크래시하는 대신 안전하게
      SQLite로 전환되어 최소한의 로컬 운영이 계속되도록 합니다.
    - get_db_type() / is_postgres_active() 헬스체크·상태조회용 헬퍼 추가.
"""

from dataclasses import dataclass, field
from typing import Any

from core.logger import setup_logger

logger = setup_logger("container")

from data.db_manager import DatabaseManager
from data.kiwoom_connector import KiwoomConnectorV512
from report.telegram_sender import TelegramSender
from scanner.deep_analyzer import DeepAnalyzer
from orchestrator.portfolio_manager import PortfolioManager
from analytics.performance_tracker import performance_tracker
from execution.order_executor import OrderExecutor


@dataclass
class AppContainer:
    """애플리케이션 의존성 컨테이너 (v1.3: PostgreSQL 스위칭 + 자동 폴백)"""

    _db_manager: Any | None = field(default=None, init=False)
    _kiwoom: KiwoomConnectorV512 | None = field(default=None, init=False)
    _telegram: TelegramSender | None = field(default=None, init=False)
    _portfolio_manager: PortfolioManager | None = field(default=None, init=False)
    _deep_analyzer: DeepAnalyzer | None = field(default=None, init=False)
    _order_executor: OrderExecutor | None = field(default=None, init=False)

    config: dict = field(default_factory=dict)

    # ============================================================
    # 프로퍼티 (지연 초기화)
    # ============================================================

    @property
    def db_manager(self) -> Any:
        """DATABASE_URL 설정 여부에 따라 PostgresManager 또는 SQLite DatabaseManager 반환.

        infrastructure/database/postgres_manager.py의 get_active_db_manager()를
        그대로 재사용합니다(단일 진실 소스). 두 클래스는 동일한 public API를
        가지므로 상위 코드(bootstrap.py 등)는 어떤 DB가 활성화되어 있는지
        전혀 알 필요 없이 투명하게 동작합니다.
        """
        if self._db_manager is None:
            try:
                from infrastructure.database.postgres_manager import get_active_db_manager
                self._db_manager = get_active_db_manager()
            except ImportError as e:
                self._db_manager = DatabaseManager()
                logger.warning(f"postgres_manager 모듈 임포트 실패({e}) → SQLite 강제 사용")
        return self._db_manager

    @property
    def kiwoom(self) -> KiwoomConnectorV512:
        if self._kiwoom is None:
            self._kiwoom = KiwoomConnectorV512()
        return self._kiwoom

    @property
    def telegram(self) -> TelegramSender:
        if self._telegram is None:
            self._telegram = TelegramSender()
        return self._telegram

    @property
    def portfolio_manager(self) -> PortfolioManager:
        if self._portfolio_manager is None:
            self._portfolio_manager = PortfolioManager()
        return self._portfolio_manager

    @property
    def deep_analyzer(self) -> DeepAnalyzer:
        if self._deep_analyzer is None:
            self._deep_analyzer = DeepAnalyzer(db_manager=self.db_manager)
        return self._deep_analyzer

    @property
    def order_executor(self) -> OrderExecutor:
        if self._order_executor is None:
            self._order_executor = OrderExecutor(
                kiwoom_connector=self.kiwoom,
                db_manager=self.db_manager,
                telegram_sender=self.telegram,
                mode="paper",
            )
        return self._order_executor

    # ============================================================
    # 팩토리 메서드
    # ============================================================

    @classmethod
    def create_production(cls) -> "AppContainer":
        from core.config import get_config
        container = cls()
        container.config = get_config().get_all()
        return container

    @classmethod
    def create_test(cls, tmp_path: Any = None) -> "AppContainer":
        """테스트용 컨테이너 — 항상 SQLite 사용 (DATABASE_URL과 무관, 결정론적 테스트 보장)."""
        container = cls()
        if tmp_path:
            container._db_manager = DatabaseManager(db_path=tmp_path / "test.db")
        return container

    # ============================================================
    # 초기화 및 종료 (비동기)
    # ============================================================

    async def initialize(self):
        """DI 컨테이너 초기화 (v1.3: Postgres 연결 실패 시 SQLite 자동 폴백)."""
        try:
            await self.db_manager.init_db()
        except Exception as e:
            if self.is_postgres_active():
                logger.error(
                    f"❌ PostgreSQL 연결 실패({e}) → SQLite로 자동 폴백합니다. "
                    f"Docker 컨테이너가 실행 중인지 확인하세요 (docker-compose up -d)."
                )
                self._db_manager = DatabaseManager()
                await self._db_manager.init_db()
            else:
                raise

        logger.info(f"📦 DB 초기화 완료 ({self.get_db_type()})")

        # 🔥 절대 제거 금지: bootstrap.py의 start_performance_tracker()가
        # 이 호출이 이미 실행되었다는 것을 전제로 설계되어 있습니다.
        performance_tracker.initialize(self.db_manager)

        await self.order_executor.initialize()
        await self.portfolio_manager.start()
        logger.info("✅ DI 컨테이너 초기화 완료")

    async def shutdown(self):
        await self.portfolio_manager.stop()
        await self.kiwoom.disconnect()
        await self.db_manager.close()
        await performance_tracker.stop()
        logger.info("🛑 DI 컨테이너 종료 완료")

    # ============================================================
    # 상태 조회 (헬스체크 / Telegram 상태 명령어용)
    # ============================================================

    def is_postgres_active(self) -> bool:
        """PostgreSQL이 실제로 활성화되어 있는지 반환 (캐시된 인스턴스 기준, 재생성 없음)."""
        if self._db_manager is None:
            return False
        return type(self._db_manager).__name__ == "PostgresManager"

    def get_db_type(self) -> str:
        """현재 사용 중인 DB 타입 반환."""
        return "PostgreSQL" if self.is_postgres_active() else "SQLite"
