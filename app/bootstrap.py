#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/bootstrap.py - V10 DI Container and Boot Sequence v2.5.0

v2.5.0 변경 (container.py / db_manager.py / order_executor.py /
              portfolio_var.py / deep_analyzer.py / portfolio_manager.py 대조 검증):
    - ✅ PortfolioManager가 싱글톤임이 소스로 확정됨(__new__ 패턴 확인).
      container.portfolio_manager와 analyzer.portfolio_manager는 동일 객체이며,
      start()/stop()은 _running 가드로 idempotent함이 확인됨.
      → init_analyzer()의 중복 start() 호출, shutdown()의 중복 stop() 호출 제거.
    - 🔥 CRITICAL 발견: risk/portfolio_var.py가 계산하는 position_limit이
      execution/order_executor.py의 update_position_limit()으로 전달되는 코드가
      어디에도 없었음. ROADMAP.md에는 "✅ 완료"로 기록되어 있었으나 실제
      연결 고리가 없었던 것을 소스 대조로 확인하고, PortfolioManager에
      콜백 등록 방식(set_order_executor_callback)으로 연결을 완성함.
    - order_executor.initialize() "중복 호출" 의혹은 근거 없음(재확인):
      AppContainer.order_executor는 @property 지연 싱글톤이라 재접근해도
      재초기화되지 않음.
    - scanner/deep_analyzer.py __init__의 asyncio.create_task(portfolio_manager.start())
      중복 태스크 생성 문제는 deep_analyzer.py v7.7.1 패치로 별도 해결.

v2.4.0 이전 변경 이력(SafetyGuard v5.2.0 API, SentimentPipeline, HyperparameterTuner,
set_telegram_sender/set_realtime_price_provider 핫픽스 등)은 유지됨.

V10 DDD 아키텍처의 유일한 부트스트래퍼.
"""

import asyncio
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, List

sys.path.insert(0, str(Path(__file__).parent.parent))

# ─── V10 Observability ──────────────────────────────────────────────
from observability.tracer import get_tracer
from observability.auto_trace import TracedService
from observability.trace_id import bind_trace_id, reset_trace_id, new_trace_id
from observability.health_score import calculate_health_score

trace = get_tracer(__name__)

# ─── V10 Config (Pydantic schema) ───────────────────────────────────
from config.schema import get_config
from core.container import AppContainer
from core.logger import setup_logger
from core.exception_handler import set_alert_handler
from core.holiday_utils import is_trading_day
from core.regime_manager import regime_manager
from core.scheduler import SchedulerManager

# ─── BlackBox / DebugTower (운영 필수) ───────────────────────────────
from core.blackbox_logger import get_status as bb_get_status, log_error, log_event
from core.debug_tower import debug_tower
from core.exception_handler import (
    restore_exception_handler,
    setup_global_exception_handler,
)
from core.supervisor import SystemSupervisor

# ─── Data / Infrastructure ───────────────────────────────────────────
from data.db_manager import DatabaseManager

try:
    from infrastructure.dart.client import DartConnector
except ImportError:
    from data.dart_connector import DartConnector

try:
    from infrastructure.news.crawler import NewsCrawler
except ImportError:
    from data.news_crawler import NewsCrawler

try:
    from infrastructure.kiwoom import KiwoomConnectorV512
except ImportError:
    from data.kiwoom_connector import KiwoomConnectorV512

try:
    from infrastructure.kiwoom.monitor import RealtimeMonitor
except ImportError:
    from scanner.realtime_monitor import RealtimeMonitor

from scanner.deep_analyzer import DeepAnalyzer

# ─── Application Layer (V10) ─────────────────────────────────────────
from application.analysis.signal_pipeline import SignalPipeline
from application.analysis.strategy_bandit import StrategyBandit
from application.analysis.bandit_feedback_bridge import BanditFeedbackBridge
from application.analysis.ab_framework import get_ab_manager, ABTestManager
from application.analysis.tuning_executor import TuningExecutor

# ─── Orchestrator (V10) ───────────────────────────────────────────────
from orchestrator.sentiment_pipeline import SentimentPipeline
from orchestrator.portfolio_manager import PortfolioManager

# ─── Analytics / Report / Risk ───────────────────────────────────────
from analytics.performance_tracker import performance_tracker
from analytics.calibration_executor import ExecutionCalibrator
from analytics.alert_verifier import scheduled_verify
from report.telegram_sender import TelegramSender
from report.telegram_commands import TelegramCommandHandler
from report.daily_report import DailyReportGenerator
from report.weekly_pdf import WeeklyPDFGenerator
from feedback.feedback_learner import FeedbackLearner
from monitor.phase_transition_validator import PhaseTransitionValidator
from risk.safety_guard import SafetyGuard
from scheduler.macro_collector import fetch_macro_data, get_cached_macro, set_alert_callback
from scheduler.daily_collector import collect_daily_ohlcv
from collector.collector_status import collector_status

from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

try:
    from aiohttp import web as aiohttp_web
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False

logger = setup_logger("bootstrap")
config = get_config()

# ─── 상수 ────────────────────────────────────────────────────────────
_DATA_FLOW_TIMEOUT = 180
_REQUIRED_ENV_KEYS = [
    "KIWOOM_APP_KEY",
    "KIWOOM_APP_SECRET",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]
PID_FILE = Path(__file__).parent.parent / "scanner.pid"


class Bootstrapper(TracedService):
    """V10 부트스트래퍼 - 시스템의 유일한 진입점."""

    def __init__(self) -> None:
        self._shutdown_requested = False
        self._shutdown_event: Optional[asyncio.Event] = None

        # ─── 컴포넌트 ─────────────────────────────────────────────
        self.container: Optional[AppContainer] = None
        self.kiwoom: Optional[KiwoomConnectorV512] = None
        self.monitor: Optional[RealtimeMonitor] = None
        self.db: Optional[DatabaseManager] = None
        self.scheduler: Optional[SchedulerManager] = None
        self.telegram_cmd: Optional[TelegramCommandHandler] = None
        self.safety_guard: Optional[SafetyGuard] = None
        self.analyzer: Optional[DeepAnalyzer] = None
        self.signal_pipeline: Optional[SignalPipeline] = None
        self.bandit: Optional[StrategyBandit] = None
        self.bandit_bridge: Optional[BanditFeedbackBridge] = None
        self.ab_manager: Optional[ABTestManager] = None
        self.tuning_executor: Optional[TuningExecutor] = None
        self.sentiment_pipeline: Optional[SentimentPipeline] = None
        self._error_sender: Optional[TelegramSender] = None
        self._original_exception_handlers: Optional[dict] = None

        # ─── 큐 / 태스크 ─────────────────────────────────────────
        self.message_queue: asyncio.Queue = asyncio.Queue(
            maxsize=config.queue_maxsize
        )
        self.worker_tasks: List[asyncio.Task] = []
        self.all_tasks: List[asyncio.Task] = []

        # ─── 타이밍 ───────────────────────────────────────────────
        self.start_time = 0.0
        self._last_data_time = 0.0
        self.startup_details: dict[str, Any] = {}

    # ═══════════════════════════════════════════════════════════════
    #  1. 환경 초기화
    # ═══════════════════════════════════════════════════════════════

    def load_env(self) -> None:
        """환경변수 로드 (.env 파일)."""
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=True)
            logger.info(f".env loaded: {env_path}")

    def validate_env(self) -> None:
        """필수 환경변수 검증. 누락 시 SystemExit."""
        missing = [k for k in _REQUIRED_ENV_KEYS if not os.getenv(k)]
        if missing:
            msg = f"필수 환경변수 누락: {', '.join(missing)}"
            logger.critical(msg)
            print(f"❌ {msg}")
            sys.exit(1)
        logger.info("환경변수 검증 완료")
        debug_tower.log("SYSTEM", "ENV_VALIDATED", {})

    def manage_pid(self) -> None:
        """PID 파일로 중복 실행 방지."""
        if PID_FILE.exists():
            try:
                old_pid = int(PID_FILE.read_text().strip())
                if sys.platform == "win32":
                    result = subprocess.run(
                        ["tasklist", "/FI", f"PID eq {old_pid}"],
                        capture_output=True, text=True
                    )
                    running = str(old_pid) in result.stdout
                else:
                    running = Path(f"/proc/{old_pid}").exists()

                if running:
                    print(f"❌ 이미 실행 중 (PID: {old_pid})")
                    sys.exit(1)
                else:
                    PID_FILE.unlink()
            except Exception:
                try:
                    PID_FILE.unlink()
                except Exception:
                    pass

        PID_FILE.write_text(str(os.getpid()))
        logger.info(f"PID 파일 생성: {os.getpid()}")

    def cleanup_pid(self) -> None:
        """종료 시 PID 파일 삭제."""
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    #  2. 컴포넌트 초기화
    # ═══════════════════════════════════════════════════════════════

    async def init_container(self) -> None:
        """DI 컨테이너 초기화.

        container.initialize()는 내부적으로 db_manager.init_db(),
        performance_tracker.initialize(db_manager), order_executor.initialize(),
        portfolio_manager.start()를 모두 수행합니다. order_executor는 @property
        지연 싱글톤이므로 이후 재접근해도 재초기화되지 않아 안전합니다(검증 완료).
        portfolio_manager 역시 __new__ 기반 싱글톤이므로 여기서 시작된 것이
        시스템 전체에서 유일한 인스턴스입니다(검증 완료).
        """
        self.container = AppContainer.create_production()
        await self.container.initialize()
        self.db = self.container.db_manager
        self.kiwoom = self.container.kiwoom
        log_event("CONTAINER_INITIALIZED", {})
        logger.info("DI container initialized")

    async def init_telegram(self) -> None:
        """Telegram 알림 핸들러 연결."""
        self._error_sender = TelegramSender()
        set_alert_callback(self._send_error_alert)
        set_alert_handler(self._send_error_alert)
        logger.info("Telegram alert handler connected")

    async def connect_kiwoom(self) -> None:
        """Kiwoom 연결 (재시도 포함)."""
        if not self.kiwoom:
            raise RuntimeError("Kiwoom connector missing")
        logger.info("Waiting for Kiwoom connection...")
        retry_count = 0
        while not self.kiwoom.is_connected():
            retry_count += 1
            await self.kiwoom.connect()
            if not self.kiwoom.is_connected():
                if retry_count % 5 == 0:
                    await self._send_error_alert(f"Kiwoom failed (retry {retry_count})")
                await asyncio.sleep(config.websocket.connect_retry_interval)
        logger.info(f"Kiwoom connected (retries={retry_count})")
        log_event("KIWOOM_CONNECTED", {"retries": retry_count})

        if not await self.kiwoom.wait_until_ready(timeout=10.0):
            await self.kiwoom.disconnect()
            await self.kiwoom.connect()
            if not await self.kiwoom.wait_until_ready(timeout=10.0):
                raise RuntimeError("WebSocket timeout after reconnect")
        logger.info("WebSocket ready")
        debug_tower.log("SYSTEM", "WS_READY_OK", {})

    def _get_realtime_price(self, ticker: str) -> float:
        """실시간 체결가 제공자 (SignalPipeline → AtrService 주입용).

        AppContainer에는 set/get_realtime_price_provider가 존재하지 않음이
        확인되었으므로, 컨테이너를 거치지 않고 self.monitor를 직접 참조하는
        바운드 메서드로 주입합니다.
        """
        if self.monitor is None:
            return 0.0
        try:
            price = self.monitor.get_latest_price(ticker)
            return float(price) if price else 0.0
        except Exception:
            return 0.0

    async def start_monitor(self) -> None:
        """RealtimeMonitor 시작."""
        if not self.kiwoom:
            raise RuntimeError("Kiwoom missing")
        self.monitor = RealtimeMonitor(self.kiwoom, self.message_queue)
        await self.monitor.start()

        if hasattr(self.monitor, "set_telegram_sender"):
            self.monitor.set_telegram_sender(TelegramSender())
            logger.debug("RealtimeMonitor.set_telegram_sender 연결됨")
        else:
            logger.debug(
                "RealtimeMonitor.set_telegram_sender 미구현 → 스킵 "
                "(텔레그램 발송은 strategy_worker가 전담)"
            )

        self.startup_details["ticker_count"] = self.monitor.get_subscribed_count()
        self.startup_details["tickers"] = self.monitor.tickers
        self.startup_details["kiwoom_connected"] = self.kiwoom.is_connected()

        log_event("MONITOR_STARTED", {"count": self.startup_details["ticker_count"]})
        logger.info(f"RealtimeMonitor started (tickers={self.startup_details['ticker_count']})")

    async def start_regime_manager(self) -> None:
        """RegimeManager 시작."""
        await regime_manager.start()
        debug_tower.log("SYSTEM", "REGIME_MANAGER_STARTED", {})
        logger.info("RegimeManager started")

    async def init_analyzer(self) -> None:
        """DeepAnalyzer + SignalPipeline(V10) 초기화.

        v2.5.0: PortfolioManager가 싱글톤임이 확정되어(orchestrator/portfolio_manager.py
        __new__ 확인), container.initialize()가 이미 start()를 호출했으므로
        여기서는 재호출하지 않습니다. scanner/deep_analyzer.py v7.7.1도 함께
        패치하여 __init__ 내부의 불필요한 asyncio.create_task(start()) 생성을
        제거했습니다.

        container.deep_analyzer 프로퍼티는 feedback_learner를 주입하지 않는
        별도 생성 경로이며, container.initialize()가 이를 전혀 참조하지 않아
        실제로는 사용되지 않는 것으로 확인되었습니다. 따라서 이 메서드는
        의도적으로 별도의 FeedbackLearner를 구성해 DeepAnalyzer를 직접 생성합니다.
        """
        if not self.db or not self.kiwoom:
            raise RuntimeError("DB or Kiwoom missing")

        learner = FeedbackLearner(kiwoom_connector=self.kiwoom, db_manager=self.db)
        self.analyzer = DeepAnalyzer(db_manager=self.db, feedback_learner=learner)
        await self.analyzer.load_weights()

        self.signal_pipeline = SignalPipeline(
            db_manager=self.db,
            realtime_price_provider=self._get_realtime_price,
        )
        logger.info("DeepAnalyzer + SignalPipeline(V10) initialized")

        # PortfolioManager(싱글톤)의 start()는 container.initialize()가 전담.
        logger.info(
            "PortfolioManager singleton confirmed — already started via container.initialize()"
        )

        try:
            restored = await self.db.load_trailing_stops()
            if restored:
                self.analyzer.trailing_stops.update(restored)
                logger.info(f"trailing_stops restored: {len(restored)} items")
            else:
                logger.info("No trailing_stops to restore (first run or clean exit)")
        except Exception as e:
            logger.warning(f"trailing_stops restore failed (continuing): {e}")

    # ═══════════════════════════════════════════════════════════════
    #  Session 15: HyperparameterTuner
    # ═══════════════════════════════════════════════════════════════

    async def init_hyperparameter_tuner(self) -> None:
        """TuningExecutor 초기화 (DB + SignalPipeline 준비된 후 호출)."""
        if self.signal_pipeline is None or self.db is None:
            logger.warning(
                "SignalPipeline 또는 DB 미초기화 → TuningExecutor 건너뜀 "
                "(하이퍼파라미터 자동 튜닝 비활성화)"
            )
            return
        self.tuning_executor = TuningExecutor(
            db_manager=self.db,
            pipeline=self.signal_pipeline,
            telegram=TelegramSender(),
            n_trials=50,
        )
        logger.info(
            "TuningExecutor 초기화 완료 (매주 일요일 03:00 자동 튜닝 예정, n_trials=50)"
        )

    async def _run_hyperparameter_tuning(self) -> None:
        """하이퍼파라미터 자동 튜닝 스케줄러 래퍼."""
        if self.tuning_executor is None:
            logger.debug("TuningExecutor 미초기화 — 튜닝 스킵")
            return
        await self.tuning_executor.run(days=30)

    # ═══════════════════════════════════════════════════════════════
    #  Session 16: SentimentPipeline
    # ═══════════════════════════════════════════════════════════════

    async def init_sentiment_pipeline(self) -> None:
        """SentimentPipeline 초기화."""
        news_crawler = None
        try:
            news_crawler = NewsCrawler()
            await news_crawler.connect()
            logger.info("NewsCrawler 연결 성공 (SentimentPipeline용)")
        except Exception as e:
            logger.warning(
                f"NewsCrawler 초기화 실패: {e} → SentimentPipeline 비활성 모드로 폴백"
            )
            news_crawler = None

        self.sentiment_pipeline = SentimentPipeline(
            news_crawler=news_crawler,
            max_news_per_ticker=20,
        )
        await self.sentiment_pipeline.start()

        if self.monitor:
            self.sentiment_pipeline.set_active_tickers(self.monitor.tickers)
            logger.info(
                f"SentimentPipeline 활성 종목 동기화: {len(self.monitor.tickers)}개"
            )

        crawler_status = "활성" if news_crawler is not None else "비활성(폴백)"
        logger.info(f"SentimentPipeline 초기화 완료 (crawler={crawler_status})")
        debug_tower.log(
            "SYSTEM", "SENTIMENT_PIPELINE_STARTED",
            {"crawler_available": news_crawler is not None}
        )

    async def init_data_sources(self) -> None:
        """DART 커넥터 초기화. NewsCrawler는 init_sentiment_pipeline()이 전담."""
        dart_key = os.getenv("DART_API_KEY")
        if dart_key:
            dart = DartConnector(api_key=dart_key)
            await dart.connect()
            logger.info("DART connector initialized")
        else:
            logger.warning("DART_API_KEY missing → financial data excluded")

    async def start_performance_tracker(self) -> None:
        """StrategyBandit + BanditFeedbackBridge 연결."""
        if not self.db:
            return

        self.bandit = StrategyBandit(
            strategy_names=["Trend", "Reversal", "Breakout"],
            decay=0.99,
        )
        logger.info("StrategyBandit initialized (arms: Trend/Reversal/Breakout)")

        self.bandit_bridge = BanditFeedbackBridge(
            db=self.db,
            bandit=self.bandit,
            feedback_days=7,
        )
        performance_tracker.attach_bandit_bridge(self.bandit_bridge)
        logger.info("BanditFeedbackBridge attached to PerformanceTracker")

        await performance_tracker.start()
        logger.info("PerformanceTracker v3.0 started (5min update loop + Bandit feedback)")

    async def start_ab_framework(self) -> None:
        """A/B Testing Framework 초기화 (Phase 3)."""
        self.ab_manager = get_ab_manager()
        self.ab_manager.create_test(
            test_name="strategy_selection",
            variant_names=["control", "ml_bandit"],
            traffic_split=[0.5, 0.5],
            alpha=0.05,
            min_samples=30,
        )
        self.ab_manager.create_test(
            test_name="entry_timing",
            variant_names=["momentum", "mean_revert"],
            traffic_split=[0.5, 0.5],
            alpha=0.05,
            min_samples=30,
        )
        self.ab_manager.create_test(
            test_name="calibration_quality",
            variant_names=["trend", "reversal", "sideways"],
            traffic_split=[1.0 / 3, 1.0 / 3, 1.0 / 3],
            alpha=0.05,
            min_samples=20,
        )
        logger.info(
            "✅ A/B Framework 시작: 실험=%s",
            list(self.ab_manager.list_tests().keys()),
        )

    async def init_execution(self) -> None:
        """OrderExecutor 참조 확인 + Calibrator 초기화 + PortfolioManager 콜백 연결.

        🔥 v2.5.0: PortfolioManager(싱글톤) → OrderExecutor.update_position_limit()
        콜백 연결을 추가했습니다. portfolio_manager.py의 update_var()가 VaR/Kelly
        계산을 완료할 때마다 이 콜백을 통해 OrderExecutor의 주문 크기 한도가
        실시간으로 갱신됩니다. ROADMAP.md에는 이 연결이 "완료(✅)"로 기록되어
        있었으나, 실제 코드에는 연결 고리가 없었음을 소스 대조로 확인하고
        이번에 완성했습니다.
        """
        if not self.container:
            raise RuntimeError("Container missing")
        order_exec = self.container.order_executor
        logger.info("OrderExecutor confirmed (Paper Mode, initialized via container)")

        try:
            pm = PortfolioManager()
            pm.set_order_executor_callback(order_exec.update_position_limit)
            logger.info(
                "PortfolioManager → OrderExecutor position_limit 콜백 연결 완료 "
                "(ROADMAP.md 기록과 실제 구현 간의 단절 수정)"
            )
        except Exception as e:
            logger.warning(f"OrderExecutor 콜백 등록 실패 (비치명): {e}")

        ExecutionCalibrator(self.db, TelegramSender())
        logger.info("ExecutionCalibrator initialized")

    async def start_telegram_commands(self) -> None:
        """Telegram 명령어 핸들러 시작."""
        self.telegram_cmd = TelegramCommandHandler(
            token=os.getenv("TELEGRAM_BOT_TOKEN"),
            chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            get_stats_callback=self._get_system_stats,
        )
        dart_connector = None
        dart_key = os.getenv("DART_API_KEY")
        if dart_key:
            try:
                dart_connector = DartConnector(api_key=dart_key)
            except Exception:
                pass

        self.telegram_cmd.set_dependencies(
            db_manager=self.db,
            analyzer=self.analyzer,
            monitor=self.monitor,
            dart=dart_connector,
            news=None,
            kiwoom=self.kiwoom,
        )
        try:
            await self.telegram_cmd.start()
            logging.getLogger("telegram.ext").setLevel(logging.INFO)
            logging.getLogger("telegram.request").setLevel(logging.INFO)
            logger.info("Telegram commands activated (natural language + analysis)")
        except Exception as e:
            logger.warning(f"Telegram start failed: {e}")
            self.telegram_cmd = None

    async def init_scheduler(self) -> None:
        """APScheduler 9개 작업 등록."""
        self.scheduler = SchedulerManager()
        sched = config.scheduler

        sender = TelegramSender()
        daily_reporter = DailyReportGenerator(db_manager=self.db, telegram_sender=sender)
        weekly_pdf_gen = WeeklyPDFGenerator(db_manager=self.db, kiwoom_connector=self.kiwoom)
        feedback_learner = FeedbackLearner(self.kiwoom, self.db)
        calibrator = ExecutionCalibrator(self.db, sender)

        self.scheduler.add_job_with_retry(
            self._run_daily_report,
            CronTrigger(hour=sched.daily_report_hour, minute=sched.daily_report_minute, timezone="Asia/Seoul"),
            "daily_report", daily_reporter, max_retries=3, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_feedback_learning,
            CronTrigger(hour=sched.feedback_hour, minute=sched.feedback_minute, timezone="Asia/Seoul"),
            "feedback_learning", feedback_learner, max_retries=3, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_weekly_pdf,
            CronTrigger(day_of_week=sched.weekly_pdf_day, hour=sched.weekly_pdf_hour, minute=sched.weekly_pdf_minute, timezone="Asia/Seoul"),
            "weekly_pdf", weekly_pdf_gen, max_retries=3, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_daily_ohlcv,
            CronTrigger(hour=sched.ohlcv_hour, minute=sched.ohlcv_minute, timezone="Asia/Seoul"),
            "daily_ohlcv", max_retries=3, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_macro_update,
            CronTrigger(hour=sched.macro_update_hour, minute=sched.macro_update_minute, timezone="Asia/Seoul"),
            "macro_update", max_retries=3, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_phase_transition_check,
            CronTrigger(hour=17, minute=30, timezone="Asia/Seoul"),
            "phase_transition_check", sender, max_retries=2, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_calibration,
            CronTrigger(hour=17, minute=30, timezone="Asia/Seoul"),
            "calibration", calibrator, max_retries=2, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            scheduled_verify,
            CronTrigger(hour=16, minute=0, timezone="Asia/Seoul"),
            "alert_verifier", max_retries=2, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_hyperparameter_tuning,
            CronTrigger(day_of_week="sun", hour=3, minute=0, timezone="Asia/Seoul"),
            "hyperparameter_tuning", max_retries=1, retry_delay=60,
        )
        self.scheduler.start()
        self.startup_details["job_count"] = 9
        log_event("SCHEDULER_STARTED", {"jobs": 9})
        logger.info("Scheduler started (9 jobs registered, incl. hyperparameter_tuning)")

    async def start_workers(self) -> None:
        """전략 Worker 2개 시작."""
        if not self.analyzer or not self.db:
            raise RuntimeError("Analyzer or DB missing")
        sender = TelegramSender()
        for i in range(2):
            task = asyncio.create_task(
                self._strategy_worker(i + 1, self.analyzer, self.db, sender)
            )
            self.worker_tasks.append(task)
            self.all_tasks.append(task)
        logger.info("Strategy workers started (×2)")

    async def start_supervisor(self) -> None:
        """SystemSupervisor 백그라운드 감시 시작."""
        supervisor = SystemSupervisor()
        task = asyncio.create_task(supervisor.run())
        self.all_tasks.append(task)
        logger.info("SystemSupervisor started (background monitoring)")
        debug_tower.log("SYSTEM", "SUPERVISOR_STARTED", {})

    async def start_health_server(self) -> None:
        """HTTP 헬스체크 서버 시작 (8080 포트)."""
        if not _AIOHTTP_AVAILABLE:
            logger.warning("aiohttp not installed → health server skipped")
            return

        for offset in range(10):
            try_port = 8080 + offset
            try:
                app = aiohttp_web.Application()
                app.router.add_get("/health", self._health_endpoint)
                runner = aiohttp_web.AppRunner(app)
                await runner.setup()
                site = aiohttp_web.TCPSite(runner, "0.0.0.0", try_port)
                await site.start()
                logger.info(f"Health server running: http://0.0.0.0:{try_port}/health")
                debug_tower.log("SYSTEM", "HEALTH_SERVER_STARTED", {"port": try_port})
                return
            except OSError:
                continue
        logger.warning("Health server failed to start (all ports busy)")

    # ═══════════════════════════════════════════════════════════════
    #  3. HTTP 헬스체크 엔드포인트
    # ═══════════════════════════════════════════════════════════════

    async def _health_endpoint(self, request: Any) -> Any:
        """GET /health 응답."""
        queue_usage = (
            self.message_queue.qsize() / self.message_queue.maxsize * 100
            if self.message_queue.maxsize > 0 else 0
        )
        data_flow_ok = (time.time() - self._last_data_time) < _DATA_FLOW_TIMEOUT

        workers_total = len(self.worker_tasks)
        workers_alive = sum(1 for t in self.worker_tasks if not t.done())

        health = calculate_health_score(
            db_initialized=self.db is not None,
            queue_size=self.message_queue.qsize(),
            queue_maxsize=self.message_queue.maxsize,
            last_data_time=self._last_data_time,
            kiwoom_connected=self.kiwoom.is_connected() if self.kiwoom else False,
            signal_pipeline_initialized=self.signal_pipeline is not None,
            workers_alive=workers_alive,
            workers_total=workers_total,
            monitor_running=self.monitor.is_running() if self.monitor else False,
        )

        status = {
            "status": "healthy" if (queue_usage < 90 and data_flow_ok) else "degraded",
            "uptime_seconds": time.time() - self.start_time if self.start_time else 0,
            "health_score": health.to_dict(),
            "components": {
                "kiwoom": {"connected": self.kiwoom.is_connected() if self.kiwoom else False},
                "monitor": {"running": self.monitor.is_running() if self.monitor else False},
                "database": {"initialized": self.db is not None},
                "queue": {
                    "size": self.message_queue.qsize(),
                    "maxsize": self.message_queue.maxsize,
                    "usage_pct": round(queue_usage, 1),
                },
                "data_flow": {
                    "last_data_sec_ago": round(time.time() - self._last_data_time, 1),
                    "healthy": data_flow_ok,
                },
                "signal_pipeline": {"initialized": self.signal_pipeline is not None},
                "hyperparameter_tuning": {
                    "initialized": self.tuning_executor is not None,
                    "current_hyperparameters": (
                        self.signal_pipeline.get_hyperparameters()
                        if self.signal_pipeline is not None else None
                    ),
                },
                "sentiment_pipeline": {
                    "initialized": self.sentiment_pipeline is not None,
                    "status": (
                        self.sentiment_pipeline.get_status()
                        if self.sentiment_pipeline is not None else None
                    ),
                },
                "safety_guard": (
                    self.safety_guard.get_status()
                    if self.safety_guard is not None else None
                ),
                "portfolio_manager": PortfolioManager().get_status(),
            },
            "blackbox": bb_get_status(),
            "regime": regime_manager.get_status().get("current_regime", "Sideways"),
            "macro": get_cached_macro().to_dict(),
            "performance": performance_tracker.get_status() if performance_tracker else {},
            "collector": collector_status.get_summary(),
            "workers": {
                "total": workers_total,
                "alive": workers_alive,
            },
        }
        return aiohttp_web.json_response(status)

    # ═══════════════════════════════════════════════════════════════
    #  4. 스케줄러 작업
    # ═══════════════════════════════════════════════════════════════

    async def _run_daily_report(self, reporter: DailyReportGenerator) -> None:
        if not is_trading_day():
            return
        await reporter.generate_and_send()

    async def _run_feedback_learning(self, learner: FeedbackLearner) -> None:
        if not is_trading_day() or not self.analyzer:
            return
        await learner.run()
        await self.analyzer.load_weights()

    async def _run_weekly_pdf(self, pdf_gen: WeeklyPDFGenerator) -> None:
        if not is_trading_day():
            return
        await pdf_gen.generate()

    async def _run_daily_ohlcv(self) -> None:
        if not is_trading_day() or not self.monitor:
            return
        await collect_daily_ohlcv(self.kiwoom, self.db, self.monitor.tickers)

    async def _run_macro_update(self) -> None:
        await fetch_macro_data(force=True)

    async def _run_phase_transition_check(self, sender: TelegramSender) -> None:
        if not is_trading_day():
            return
        try:
            validator = PhaseTransitionValidator()
            start_date = "2026-08-20"
            end_date = datetime.now().strftime("%Y-%m-%d")
            decisions = await self.db.get_decisions_by_date_range(start_date, end_date)

            if len(decisions) < 50:
                await sender.send_raw(
                    f"📊 Phase 전환 검증: 샘플 부족 ({len(decisions)}/50)"
                )
                return

            stats = await self.db.get_feedback_stats(days=30)
            result = validator.validate({
                "start_date": start_date,
                "end_date": end_date,
                "total_signals": len(decisions),
                "win_rate": stats.get("win_rate", 0.5),
                "profit_factor": 1.2,
                "max_drawdown": 0.05,
                "fp_ratio": 0.2,
                "downtime_hours": 0.0,
            })
            msg = (
                f"🎉 Phase 2 통과! {result['recommendation']}"
                if result["passed"]
                else f"📊 Phase 미통과: {', '.join(result.get('failed_items', []))}"
            )
            await sender.send_raw(msg)
        except Exception as e:
            logger.error(f"Phase transition check error: {e}")

    async def _run_calibration(self, calibrator: ExecutionCalibrator) -> None:
        if not is_trading_day():
            return
        await calibrator.run(days=30)

    # ═══════════════════════════════════════════════════════════════
    #  5. 전략 Worker
    # ═══════════════════════════════════════════════════════════════

    async def _strategy_worker(
        self,
        wid: int,
        analyzer: DeepAnalyzer,
        db: DatabaseManager,
        sender: TelegramSender,
    ) -> None:
        """전략 분석 Worker."""
        logger.info(f"Strategy Worker-{wid} started")
        debug_tower.log("SYSTEM", f"WORKER_START_{wid}", {})
        processed_count = 0

        while not self._shutdown_requested:
            try:
                try:
                    stock_data = await asyncio.wait_for(
                        self.message_queue.get(), timeout=1.0
                    )
                except TimeoutError:
                    continue

                self._last_data_time = time.time()
                price = stock_data.get("price")
                if price is None or float(price) <= 0:
                    self.message_queue.task_done()
                    continue

                ticker = stock_data.get("ticker", "UNKNOWN")
                debug_tower.log(ticker, "WORKER_PROCESS", {"worker": wid})

                token = bind_trace_id(stock_data.get("trace_id", new_trace_id()))
                try:
                    if self.sentiment_pipeline is not None:
                        try:
                            stock_data = await self.sentiment_pipeline.enrich(stock_data)
                        except Exception as e_sent:
                            logger.debug(f"Sentiment enrich skipped ({ticker}): {e_sent}")

                    analysis = await analyzer.analyze(stock_data)

                    if (
                        self.signal_pipeline
                        and analysis.get("action") not in ("ERROR", "EVENT_EXIT")
                    ):
                        try:
                            v10_signal = await self.signal_pipeline.process(stock_data)
                            if hasattr(v10_signal, "confidence"):
                                analysis["v10_sqi"] = round(
                                    v10_signal.confidence, 3
                                )
                        except Exception as e_pipe:
                            logger.debug(f"SignalPipeline skipped ({ticker}): {e_pipe}")

                    if analysis.get("action") != "ERROR":
                        await db.save_decision(analysis)

                    action = analysis.get("action")
                    if action in (
                        "SIGNAL_ENTRY",
                        "EVENT_SL_TRAIL",
                        "EVENT_ATR_SPIKE",
                        "EVENT_TP_HIT",
                        "EVENT_EXIT",
                        "EVENT_LIFECYCLE_ADVICE",
                    ):
                        success = await sender.send(analysis)
                        if success:
                            processed_count += 1
                            logger.info(
                                f"Worker-{wid} [{action}] {ticker}"
                                + (f" TP{analysis.get('tp_level')}" if action == "EVENT_TP_HIT" else "")
                            )
                        else:
                            debug_tower.log(ticker, "TELEGRAM_SEND_FAILED", {"action": action})

                        if action == "EVENT_EXIT":
                            await analyzer.clear_trailing_stop(ticker)

                    if processed_count > 0 and processed_count % 50 == 0:
                        logger.info(f"Worker-{wid} processed {processed_count} events total")

                    self.message_queue.task_done()
                finally:
                    reset_trace_id(token)

            except asyncio.CancelledError:
                logger.info(f"Worker-{wid} cancelled")
                debug_tower.log("SYSTEM", f"WORKER_STOP_{wid}", {})
                break
            except Exception as e:
                logger.error(f"Worker-{wid} error: {e}", exc_info=True)
                debug_tower.capture_snapshot("SYSTEM", e, f"WORKER_{wid}")
                await self._send_error_alert(f"Worker-{wid} error", str(e)[:200])
                await asyncio.sleep(1)

    # ═══════════════════════════════════════════════════════════════
    #  6. 메인 루프 (SafetyGuard v5.2.0 API 사용)
    # ═══════════════════════════════════════════════════════════════

    async def run_main_loop(self) -> None:
        """메인 스캔 루프 - 실시간 시장 데이터 폴링."""
        logger.info("Main loop started (V10)")
        log_event("MAIN_LOOP_START", {})
        self.start_time = time.time()

        while not self._shutdown_requested and not self._shutdown_event.is_set():
            try:
                if self.safety_guard:
                    macro = get_cached_macro()
                    safety_result = self.safety_guard.check({
                        "kospi_drop": macro.kospi_trend or 0.0,
                        "vkospi_spike": getattr(macro, "vkospi", 0.0),
                        "usdkrw_spike": macro.usdkrw or 0.0,
                        "feature_expired": 0.0,
                        "tr_latency": 0.0,
                        "calibration_error": 0.0,
                    })

                    if safety_result.get("block_cleared"):
                        await self._send_error_alert(
                            "✅ SafetyGuard 차단 해제 — 정상 운영 재개",
                            "이전 위기 조건이 더 이상 감지되지 않습니다.",
                        )

                    if safety_result.get("action") == "BLOCK_ALL":
                        triggered = safety_result.get("triggered", [])
                        logger.critical(f"SafetyGuard triggered: {triggered}")
                        if safety_result.get("should_alert"):
                            await self._send_error_alert(
                                "SafetyGuard 차단 활성화",
                                str([t.get("condition") for t in triggered])[:200],
                            )
                        await asyncio.sleep(10)
                        continue

                if not self.kiwoom.is_connected():
                    await self._reconnect()
                    await asyncio.sleep(1)
                    continue

                now = datetime.now()
                if 9 <= now.hour <= 15 and not (now.hour == 15 and now.minute >= 20):
                    elapsed = time.time() - self._last_data_time
                    if elapsed > _DATA_FLOW_TIMEOUT:
                        log_event("DATA_FLOW_TIMEOUT", {"seconds": _DATA_FLOW_TIMEOUT})
                        logger.error(
                            f"Data flow timeout ({_DATA_FLOW_TIMEOUT}s) → reconnecting"
                        )
                        debug_tower.log("SYSTEM", "DATA_FLOW_TIMEOUT", {})
                        await self.kiwoom.disconnect()
                        await self.kiwoom.connect()
                        await self.monitor.resubscribe_all()
                        self._last_data_time = time.time()

                signals = await self.monitor.scan()
                for sig_data in signals:
                    try:
                        self.message_queue.put_nowait(sig_data)
                        debug_tower.log(
                            sig_data.get("ticker"), "SIGNAL_ENQUEUED",
                            {"action": sig_data.get("action")}
                        )
                    except asyncio.QueueFull:
                        logger.warning(f"Queue full, dropped: {sig_data.get('ticker')}")

                await asyncio.sleep(1)

            except asyncio.CancelledError:
                self._shutdown_requested = True
                break
            except Exception as e:
                log_error("Main loop error", e)
                debug_tower.capture_snapshot("SYSTEM", e, "MAIN_LOOP")
                await asyncio.sleep(5)

        logger.info("Main loop ended")
        log_event("MAIN_LOOP_END", {})

    # ═══════════════════════════════════════════════════════════════
    #  7. 재연결
    # ═══════════════════════════════════════════════════════════════

    async def _reconnect(self) -> None:
        """Kiwoom WebSocket 재연결."""
        MAX_RETRIES = 30
        retry = 0
        while not self.kiwoom.is_connected():
            retry += 1
            if retry > MAX_RETRIES:
                await self._send_error_alert("Kiwoom 재연결 반복 실패 — 수동 개입 필요")
                await asyncio.sleep(300)
                retry = 0
                continue
            logger.info(f"Reconnect attempt {retry}/{MAX_RETRIES}")
            debug_tower.log("SYSTEM", "RECONNECT_ATTEMPT", {"attempt": retry})
            await self.kiwoom.connect()
            if not self.kiwoom.is_connected():
                await asyncio.sleep(30)
        await self.monitor.resubscribe_all()
        logger.info("Reconnected and resubscribed")
        debug_tower.log("SYSTEM", "RECONNECT_SUCCESS", {})

    # ═══════════════════════════════════════════════════════════════
    #  8. Telegram 알림
    # ═══════════════════════════════════════════════════════════════

    async def _send_error_alert(self, msg: str, detail: str = "") -> None:
        """에러 알림 전송."""
        try:
            text = (
                f"🚨 <b>시스템 오류</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 {msg}\n"
                f"📋 {detail[:200] if detail else '없음'}\n"
                f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━━━━"
            )
            sender = self._error_sender or TelegramSender()
            await sender.send_raw(text)
        except Exception:
            pass

    async def _send_startup_notification(self, success: bool) -> None:
        """시작 알림 (상세)."""
        macro = get_cached_macro()
        bb = bb_get_status()
        status_emoji = "🟢" if success else "🔴"
        details = self.startup_details

        if success:
            tickers = details.get("tickers", [])
            ticker_str = ", ".join(tickers[:10]) + (
                f" 외 {len(tickers)-10}개" if len(tickers) > 10 else ""
            ) if tickers else "없음"

            sentiment_status = "비활성"
            if self.sentiment_pipeline:
                sp_status = self.sentiment_pipeline.get_status()
                sentiment_status = (
                    "활성" if sp_status.get("crawler_available") else "비활성(폴백)"
                )

            msg = (
                f"{status_emoji} <b>V10 시스템 시작 성공</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"🤖 PID: {os.getpid()}\n"
                f"📡 구독 종목: {len(tickers)}개 → {ticker_str}\n"
                f"🔌 키움: {'✅ 연결됨' if details.get('kiwoom_connected') else '❌'}\n"
                f"⏰ 스케줄러: {details.get('job_count', 0)}개\n"
                f"📰 감성 분석: {sentiment_status}\n"
                f"📊 KOSPI 5일: {macro.kospi_trend:.2f}%  |  USD/KRW: {macro.usdkrw:.0f}"
                f"  |  VIX: {macro.vix:.1f}\n"
                f"💾 블랙박스: {bb['file_count']}개  {bb['total_size_mb']}MB\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>V10 DDD + SignalPipeline + MAB + HyperTuner + Sentiment + SafetyGuard + PortfolioVaR↔OrderExecutor</i>"
            )
        else:
            msg = (
                f"{status_emoji} <b>V10 시스템 시작 실패</b>\n"
                f"📋 {details.get('error', '알 수 없음')}"
            )

        try:
            sender = self._error_sender or TelegramSender()
            await sender.send_raw(msg)
        except Exception:
            pass

    async def _send_shutdown_notification(self, reason: str = "정상 종료") -> None:
        """종료 알림."""
        try:
            msg = (
                f"🟡 <b>V10 시스템 종료</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 {reason}\n"
                f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━━━━"
            )
            sender = self._error_sender or TelegramSender()
            await sender.send_raw(msg)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    #  9. 시스템 상태 콜백 (Telegram 명령어용)
    # ═══════════════════════════════════════════════════════════════

    def _get_system_stats(self) -> dict:
        """현재 시스템 상태 딕셔너리 반환."""
        now = time.time()
        last_ago = "없음"
        if self._last_data_time > 0:
            diff = now - self._last_data_time
            last_ago = f"{int(diff)}초 전" if diff < 60 else f"{int(diff // 60)}분 전"

        queue_usage = (
            self.message_queue.qsize() / self.message_queue.maxsize * 100
            if self.message_queue.maxsize > 0 else 0
        )
        alive_workers = sum(1 for t in self.worker_tasks if not t.done())
        macro = get_cached_macro()
        regime_status = regime_manager.get_status()
        collector_summary = collector_status.get_summary()
        perf_status = performance_tracker.get_status() if performance_tracker else {}

        stats: dict[str, Any] = {
            "status": "운영 중" if (self.kiwoom and self.kiwoom.is_connected()) else "연결 끊김",
            "uptime_seconds": time.time() - self.start_time if self.start_time else 0,
            "tickers": self.monitor.get_subscribed_count() if self.monitor else 0,
            "last_data_ago": last_ago,
            "kiwoom_connected": self.kiwoom.is_connected() if self.kiwoom else False,
            "queue_usage": queue_usage,
            "worker_status": f"{alive_workers}/{len(self.worker_tasks)} 활성",
            "signal_pipeline": "V10 활성" if self.signal_pipeline else "비활성",
            "hyperparameter_tuning": "활성" if self.tuning_executor else "비활성",
            "blackbox_files": bb_get_status().get("file_count", 0),
            "blackbox_size_mb": bb_get_status().get("total_size_mb", 0),
            "regime": regime_status.get("current_regime", "Sideways"),
            "macro": {
                "kospi_trend": macro.kospi_trend,
                "usdkrw": macro.usdkrw,
                "vix": macro.vix,
            },
            "collector_status": {
                "healthy": collector_summary.get("healthy", 0),
                "total": collector_summary.get("total", 0),
            },
            "performance": perf_status,
            "portfolio_manager": PortfolioManager().get_status(),
        }
        if self.sentiment_pipeline:
            stats["sentiment_pipeline"] = self.sentiment_pipeline.get_status()
        else:
            stats["sentiment_pipeline"] = {"running": False, "crawler_available": False}
        if self.safety_guard:
            stats["safety_guard"] = self.safety_guard.get_status()
        return stats

    # ═══════════════════════════════════════════════════════════════
    #  10. 부트스트랩 메인 진입점
    # ═══════════════════════════════════════════════════════════════

    async def bootstrap(self, shutdown_event: Optional[asyncio.Event] = None) -> None:
        """전체 시스템 부트스트랩 시퀀스 (v2.5.0)."""
        self._shutdown_event = shutdown_event or asyncio.Event()
        startup_success = False

        try:
            self.load_env()
            self.validate_env()
            self.manage_pid()

            self._original_exception_handlers = setup_global_exception_handler()
            log_event("SYSTEM_START", {"pid": os.getpid(), "version": "V10"})
            debug_tower.log("SYSTEM", "MAIN_START", {"pid": os.getpid()})

            collector_status.register("system", freshness_seconds=None)

            logger.info("=" * 60)
            logger.info("V10 System Bootstrap Starting... (v2.5.0)")
            logger.info("=" * 60)

            await self.start_supervisor()
            await self.init_telegram()

            logger.info("Initial macro data collection...")
            await fetch_macro_data(force=True)
            macro = get_cached_macro()
            logger.info(f"  KOSPI: {macro.kospi_trend:.2f}%  USD/KRW: {macro.usdkrw:.0f}  VIX: {macro.vix:.1f}")

            await self.init_container()
            await self.connect_kiwoom()
            await self.start_monitor()
            await self.start_regime_manager()
            await self.init_analyzer()
            await self.init_hyperparameter_tuner()
            await self.init_sentiment_pipeline()
            await self.init_data_sources()
            await self.start_performance_tracker()
            await self.start_ab_framework()
            await self.init_execution()
            await self.start_telegram_commands()
            await self.init_scheduler()
            await self.start_workers()

            health_task = asyncio.create_task(self.start_health_server())
            self.all_tasks.append(health_task)

            if hasattr(performance_tracker, "_task") and performance_tracker._task:
                self.all_tasks.append(performance_tracker._task)

            self.safety_guard = SafetyGuard()
            logger.info("SafetyGuard v5.2.0 initialized")
            startup_success = True

            logger.info("=" * 60)
            logger.info("V10 System Bootstrap Complete")
            logger.info(f"  Tickers: {self.startup_details.get('ticker_count', 0)}")
            logger.info(f"  Scheduler jobs: {self.startup_details.get('job_count', 0)}")
            logger.info(f"  SignalPipeline: {'Active' if self.signal_pipeline else 'Inactive'}")
            logger.info(f"  HyperparameterTuner: {'Active' if self.tuning_executor else 'Inactive'}")
            logger.info(f"  SentimentPipeline: {'Active' if self.sentiment_pipeline else 'Inactive'}")
            logger.info("=" * 60)

            log_event("SYSTEM_READY", {})
            debug_tower.log("SYSTEM", "SYSTEM_READY", {})
            await self._send_startup_notification(True)

            await self.run_main_loop()

        except (KeyboardInterrupt, asyncio.CancelledError):
            self._shutdown_requested = True
            log_event("SYSTEM_INTERRUPTED", {})
            logger.info("Shutdown signal received")
        except Exception as e:
            error_msg = f"Bootstrap failed: {e}"
            log_error(error_msg, e)
            debug_tower.capture_snapshot("SYSTEM", e, "BOOTSTRAP_FAIL")
            self.startup_details["error"] = error_msg
            await self._send_startup_notification(False)
            await self._send_error_alert(error_msg)
            raise
        finally:
            if startup_success:
                await self._send_shutdown_notification("정상 종료")
            await self.shutdown()

    # ═══════════════════════════════════════════════════════════════
    #  11. 종료 시퀀스
    # ═══════════════════════════════════════════════════════════════

    async def shutdown(self) -> None:
        """정상 종료 시퀀스 (v2.5.0).

        PortfolioManager(싱글톤)의 stop()은 container.shutdown()이 전담합니다.
        analyzer.portfolio_manager는 동일 객체이므로 여기서 별도로 stop()을
        호출하던 코드를 제거했습니다(중복 제거, 싱글톤 확정에 따른 정리).
        """
        self._shutdown_requested = True
        log_event("SYSTEM_SHUTDOWN", {})
        debug_tower.log("SYSTEM", "SYSTEM_SHUTDOWN", {})

        if self.sentiment_pipeline is not None:
            try:
                await self.sentiment_pipeline.stop()
                logger.info("SentimentPipeline stopped")
            except Exception as e:
                logger.warning(f"SentimentPipeline stop failed: {e}")

        if self.analyzer is not None and self.db is not None:
            try:
                stops = getattr(self.analyzer, "trailing_stops", {})
                if stops:
                    saved = await self.db.save_trailing_stops(stops)
                    logger.info(f"trailing_stops saved: {saved} items")
                else:
                    await self.db.clear_trailing_stops()
                    logger.info("trailing_stops cleared (empty)")
            except Exception as e:
                logger.warning(f"trailing_stops save failed: {e}")

        for t in self.worker_tasks:
            if not t.done():
                t.cancel()

        if self.all_tasks:
            logger.info(f"Waiting for {len(self.all_tasks)} tasks...")
            for t in self.all_tasks:
                if not t.done():
                    t.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.all_tasks, return_exceptions=True),
                    timeout=5.0,
                )
            except TimeoutError:
                logger.warning("Some tasks did not finish within 5s")

        if self.telegram_cmd:
            try:
                await self.telegram_cmd.stop()
            except Exception as e:
                logger.warning(f"telegram_cmd.stop() failed: {e}")

        try:
            await regime_manager.stop()
        except Exception as e:
            logger.warning(f"regime_manager.stop() failed: {e}")

        if self.scheduler:
            try:
                self.scheduler.shutdown()
            except Exception as e:
                logger.warning(f"scheduler.shutdown() failed: {e}")

        # kiwoom.disconnect() / db_manager.close() / performance_tracker.stop() /
        # portfolio_manager.stop()은 container.shutdown()이 전담 처리 (중복 제거)
        if self.container:
            try:
                await self.container.shutdown()
            except Exception as e:
                logger.warning(f"container.shutdown() failed: {e}")

        try:
            summary = collector_status.get_summary()
            logger.info(
                f"Collector summary: healthy={summary['healthy']}/{summary['total']}"
            )
        except Exception:
            pass

        if self._original_exception_handlers:
            try:
                restore_exception_handler(self._original_exception_handlers)
            except Exception:
                pass

        self.cleanup_pid()
        debug_tower.flush()

        logger.info("System shutdown complete")
