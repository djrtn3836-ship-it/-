"""
core/container.py - v1.1 (P3-3: logger 임포트 추가)
- DI 컨테이너 (싱글톤 대체)
- 모든 의존성 중앙 관리 및 지연 초기화
- logger 임포트 오류 수정
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
    """애플리케이션 의존성 컨테이너"""

    # 핵심 컴포넌트 (지연 초기화)
    _db_manager: DatabaseManager | None = field(default=None, init=False)
    _kiwoom: KiwoomConnectorV512 | None = field(default=None, init=False)
    _telegram: TelegramSender | None = field(default=None, init=False)
    _portfolio_manager: PortfolioManager | None = field(default=None, init=False)
    _deep_analyzer: DeepAnalyzer | None = field(default=None, init=False)
    _order_executor: OrderExecutor | None = field(default=None, init=False)

    # 설정
    config: dict = field(default_factory=dict)

    # ============================================================
    # 프로퍼티 (지연 초기화)
    # ============================================================
    @property
    def db_manager(self) -> DatabaseManager:
        if self._db_manager is None:
            self._db_manager = DatabaseManager()
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
        container = cls()
        if tmp_path:
            container._db_manager = DatabaseManager(db_path=tmp_path / "test.db")
        return container

    # ============================================================
    # 초기화 및 종료 (비동기)
    # ============================================================
    async def initialize(self):
        await self.db_manager.init_db()
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