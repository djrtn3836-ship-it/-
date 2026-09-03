# -*- coding: utf-8 -*-
"""
core/container.py - v1.4 (Session 27: Redis 캐시 레이어 통합)

v1.3 → v1.4 변경 사항:
    - db_manager 프로퍼티: Redis가 활성화되어 있으면 CachedDbManager로
      래핑하여 get_ohlcv() 호출에 Redis 캐시 계층을 추가합니다. Redis가
      비활성화되어 있거나 연결에 실패하면 원본 DatabaseManager/PostgresManager를
      그대로 반환하여 기존 동작에 전혀 영향을 주지 않습니다.
    - initialize(): redis_cache.init()을 DB 초기화보다 먼저 수행합니다.
      Redis 초기화 실패는 시스템 시작을 절대 막지 않습니다(비활성 모드로 계속 진행).
    - shutdown(): redis_cache.close() 추가.
    - is_postgres_active() / get_db_type(): CachedDbManager로 래핑된 경우에도
      내부 원본 DB 타입을 올바르게 판별하도록 CachedDbManager.raw_db
      프로퍼티를 사용 (private 속성 직접 접근 지양).
    - 🔥 v1.3에서 확립된 절대 규칙 유지: performance_tracker.initialize() 호출은
      절대 제거하지 않습니다(제거 시 PerformanceTracker 영구 미시작 회귀 발생).

Session 27 설계 노트 (왜 db_manager 계층에서만 캐싱하는가):
    orchestrator/feature_store.py는 app/bootstrap.py의 실제 부트스트랩
    시퀀스 어디에도 인스턴스화되지 않는 것으로 확인되었습니다(analytics/
    daily_monitor.py에서만 참조되며, DailyMonitor 자체도 Bootstrapper에서
    호출되지 않음). 반면 application/analysis/signal_pipeline.py의
    _fetch_ohlcv()는 self.db_manager.get_ohlcv()를 직접 호출하며, 이
    db_manager는 AppContainer가 실제로 생산(bootstrap.py init_container())
    하는 인스턴스입니다. 따라서 Redis 캐시를 db_manager 계층에 추가하는 것이
    실제 운영 파이프라인에 효과가 있는 유일한 지점이며, feature_store.py를
    수정하는 것은(실제 연동 여부가 재확인되기 전까지는) 실질적 효과가
    없는 작업일 위험이 있어 이번 세션 범위에서 제외합니다.
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
from infrastructure.cache.redis_cache import get_redis_cache, RedisCache
from infrastructure.cache.cached_db_manager import CachedDbManager


@dataclass
class AppContainer:
    """애플리케이션 의존성 컨테이너 (v1.4: Redis 캐시 레이어 통합)"""

    _db_manager: Any | None = field(default=None, init=False)
    _kiwoom: KiwoomConnectorV512 | None = field(default=None, init=False)
    _telegram: TelegramSender | None = field(default=None, init=False)
    _portfolio_manager: PortfolioManager | None = field(default=None, init=False)
    _deep_analyzer: DeepAnalyzer | None = field(default=None, init=False)
    _order_executor: OrderExecutor | None = field(default=None, init=False)
    _redis_cache: RedisCache | None = field(default=None, init=False)

    config: dict = field(default_factory=dict)

    # ============================================================
    # 프로퍼티 (지연 초기화)
    # ============================================================

    @property
    def db_manager(self) -> Any:
        """DATABASE_URL 설정 여부에 따라 PostgresManager 또는 SQLite DatabaseManager 반환.

        Redis가 활성화되어 있으면(REDIS_URL 설정 + 연결 성공) CachedDbManager로
        래핑하여 get_ohlcv() 호출에 캐시 계층을 추가합니다. Redis가 비활성화되어
        있으면 원본 DB 매니저를 그대로 반환합니다(기존 동작과 100% 동일).
        """
        if self._db_manager is None:
            try:
                from infrastructure.database.postgres_manager import get_active_db_manager
                raw_db = get_active_db_manager()
            except ImportError as e:
                raw_db = DatabaseManager()
                logger.warning(f"postgres_manager 모듈 임포트 실패({e}) → SQLite 강제 사용")

            cache = self._redis_cache or get_redis_cache()
            if cache.is_active:
                self._db_manager = CachedDbManager(raw_db, cache)
                logger.info("db_manager: CachedDbManager (Redis 활성)")
            else:
                self._db_manager = raw_db
                logger.info("db_manager: 직접 DB (Redis 비활성 또는 미연결)")
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
        """테스트용 컨테이너 — 항상 SQLite 사용, Redis 비활성 (결정론적 테스트 보장)."""
        container = cls()
        if tmp_path:
            container._db_manager = DatabaseManager(db_path=tmp_path / "test.db")
        return container

    # ============================================================
    # 초기화 및 종료 (비동기)
    # ============================================================

    async def initialize(self):
        """DI 컨테이너 초기화 (v1.4: Redis 초기화 추가).

        Redis 초기화는 db_manager 프로퍼티가 처음 접근되기 전에 완료되어야
        CachedDbManager 래핑 여부가 올바르게 결정됩니다. Redis 연결 실패는
        시스템 시작을 절대 막지 않으며, 비활성 모드로 계속 진행합니다.
        """
        self._redis_cache = get_redis_cache()
        redis_ok = await self._redis_cache.init()
        if redis_ok:
            logger.info("✅ Redis 캐시 레이어 활성화됨")
        else:
            logger.info("ℹ️ Redis 비활성 → 직접 DB 조회 모드로 계속 진행")

        try:
            await self.db_manager.init_db()
        except Exception as e:
            if self.is_postgres_active():
                logger.error(
                    f"❌ PostgreSQL 연결 실패({e}) → SQLite로 자동 폴백합니다. "
                    f"Docker 컨테이너가 실행 중인지 확인하세요 (docker-compose up -d)."
                )
                raw_sqlite = DatabaseManager()
                if self._redis_cache and self._redis_cache.is_active:
                    self._db_manager = CachedDbManager(raw_sqlite, self._redis_cache)
                else:
                    self._db_manager = raw_sqlite
                await self._db_manager.init_db()
            else:
                raise

        logger.info(f"📦 DB 초기화 완료 ({self.get_db_type()})")

        # 🔥 절대 제거 금지: bootstrap.py의 start_performance_tracker()가
        # 이 호출이 이미 실행되었다는 것을 전제로 설계되어 있습니다.
        performance_tracker.initialize(self.db_manager)

        await self.order_executor.initialize()
        await self.portfolio_manager.start()
        logger.info("✅ DI 컨테이너 초기화 완료 (v1.4)")

    async def shutdown(self):
        await self.portfolio_manager.stop()
        await self.kiwoom.disconnect()
        await self.db_manager.close()
        await performance_tracker.stop()
        if self._redis_cache:
            await self._redis_cache.close()
        logger.info("🛑 DI 컨테이너 종료 완료")

    # ============================================================
    # 상태 조회 (헬스체크 / Telegram 상태 명령어용)
    # ============================================================

    def _resolve_raw_db_type(self) -> str:
        """CachedDbManager로 래핑된 경우에도 내부 원본 DB 타입을 정확히 판별."""
        if self._db_manager is None:
            return "None"
        if isinstance(self._db_manager, CachedDbManager):
            return type(self._db_manager.raw_db).__name__
        return type(self._db_manager).__name__

    def is_postgres_active(self) -> bool:
        """PostgreSQL이 실제로 활성화되어 있는지 반환 (캐시된 인스턴스 기준, 재생성 없음)."""
        return self._resolve_raw_db_type() == "PostgresManager"

    def get_db_type(self) -> str:
        """현재 사용 중인 DB 타입 반환 (Redis 캐시 적용 여부 포함)."""
        base = "PostgreSQL" if self.is_postgres_active() else "SQLite"
        if isinstance(self._db_manager, CachedDbManager):
            return f"{base} + Redis"
        return base
