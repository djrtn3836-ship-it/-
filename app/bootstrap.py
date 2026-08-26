#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app/bootstrap.py - V10 DI Container and Boot Sequence v2.0

V10 DDD 아키텍처의 유일한 부트스트래퍼.
scanner_main.py의 모든 운영 기능을 통합하고 V10 계층을 완전 활용합니다.

통합된 기능 (scanner_main.py → app/bootstrap.py):
    - PID 파일 관리 (중복 실행 방지)
    - 환경변수 검증 (validate_env)
    - HTTP 헬스체크 서버 (0.0.0.0:8080/health)
    - SystemSupervisor 백그라운드 감시
    - 데이터흐름 타임아웃 감시 (180초)
    - Telegram 시작/종료 알림 (상세)
    - BlackBox 로거 + DebugTower 통합
    - SignalPipeline V10 워커 통합

아키텍처:
    app/main.py
        └── Bootstrapper.bootstrap()
              ├── PID / 환경변수 검증
              ├── DI Container (AppContainer)
              ├── Kiwoom + RealtimeMonitor
              ├── RegimeManager
              ├── DeepAnalyzer + SignalPipeline (V10)
              ├── PerformanceTracker
              ├── Scheduler (9개 작업)
              ├── Worker × 2
              ├── Health Server
              └── SystemSupervisor
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
from infrastructure.dart.client import DartConnector
from infrastructure.news.crawler import NewsCrawler

# infrastructure/kiwoom V10 마이그레이션 전 - data/ 폴백
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
_DATA_FLOW_TIMEOUT = 180          # 초: 이 시간 동안 데이터 없으면 재연결
_REQUIRED_ENV_KEYS = [
    "KIWOOM_APP_KEY",
    "KIWOOM_APP_SECRET",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]
PID_FILE = Path(__file__).parent.parent / "scanner.pid"


class Bootstrapper(TracedService):
    """V10 부트스트래퍼 - 시스템의 유일한 진입점.

    scanner_main.py의 모든 운영 기능과 app/bootstrap.py의
    V10 DDD 아키텍처를 통합한 완전한 부트스트래퍼입니다.

    Lifecycle:
        bootstrap() → run_main_loop() → shutdown()
    """

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
        self.signal_pipeline: Optional[SignalPipeline] = None   # V10 신규
        self.bandit: Optional[StrategyBandit] = None            # V10: MAB
        self.bandit_bridge: Optional[BanditFeedbackBridge] = None  # V10: 피드백 브리지
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
                # Windows: tasklist, Linux: /proc
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
        """DI 컨테이너 초기화."""
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
                await asyncio.sleep(config.connect_retry_interval)
        logger.info(f"Kiwoom connected (retries={retry_count})")
        log_event("KIWOOM_CONNECTED", {"retries": retry_count})

        if not await self.kiwoom.wait_until_ready(timeout=10.0):
            await self.kiwoom.disconnect()
            await self.kiwoom.connect()
            if not await self.kiwoom.wait_until_ready(timeout=10.0):
                raise RuntimeError("WebSocket timeout after reconnect")
        logger.info("WebSocket ready")
        debug_tower.log("SYSTEM", "WS_READY_OK", {})

    async def start_monitor(self) -> None:
        """RealtimeMonitor 시작 및 실시간 가격 제공자 연결."""
        if not self.kiwoom:
            raise RuntimeError("Kiwoom missing")
        self.monitor = RealtimeMonitor(self.kiwoom, self.message_queue)
        await self.monitor.start()
        self.monitor.set_telegram_sender(TelegramSender())

        self.startup_details["ticker_count"] = self.monitor.get_subscribed_count()
        self.startup_details["tickers"] = self.monitor.tickers
        self.startup_details["kiwoom_connected"] = self.kiwoom.is_connected()

        log_event("MONITOR_STARTED", {"count": self.startup_details["ticker_count"]})
        logger.info(f"RealtimeMonitor started (tickers={self.startup_details['ticker_count']})")

        # V10: 실시간 가격 제공자 → SignalPipeline ATR fallback 연결
        def get_realtime_price(ticker: str) -> float:
            if self.monitor:
                price = self.monitor.get_latest_price(ticker)
                return float(price) if price else 0.0
            return 0.0

        self.container.set_realtime_price_provider(get_realtime_price)
        logger.info("Realtime price provider connected (V10 ATR fallback)")

    async def start_regime_manager(self) -> None:
        """RegimeManager 시작."""
        await regime_manager.start()
        debug_tower.log("SYSTEM", "REGIME_MANAGER_STARTED", {})
        logger.info("RegimeManager started")

    async def init_analyzer(self) -> None:
        """DeepAnalyzer + SignalPipeline(V10) 초기화."""
        if not self.db or not self.kiwoom:
            raise RuntimeError("DB or Kiwoom missing")

        learner = FeedbackLearner(kiwoom_connector=self.kiwoom, db_manager=self.db)
        self.analyzer = DeepAnalyzer(db_manager=self.db, feedback_learner=learner)
        await self.analyzer.load_weights()

        # ─── V10: SignalPipeline 초기화 ──────────────────────────
        self.signal_pipeline = SignalPipeline(
            db_manager=self.db,
            realtime_price_provider=self.container.get_realtime_price_provider()
            if hasattr(self.container, "get_realtime_price_provider") else None,
        )
        logger.info("DeepAnalyzer + SignalPipeline(V10) initialized")

        # ─── PortfolioManager 시작 ────────────────────────────────
        if hasattr(self.analyzer, "portfolio_manager"):
            await self.analyzer.portfolio_manager.start()
            logger.info("PortfolioManager started (VaR update loop active)")

        # ─── trailing_stops DB 복구 ───────────────────────────────
        try:
            restored = await self.db.load_trailing_stops()
            if restored:
                self.analyzer.trailing_stops.update(restored)
                logger.info(f"trailing_stops restored: {len(restored)} items")
            else:
                logger.info("No trailing_stops to restore (first run or clean exit)")
        except Exception as e:
            logger.warning(f"trailing_stops restore failed (continuing): {e}")

    async def init_data_sources(self) -> None:
        """DART, News 크롤러 초기화."""
        dart_key = os.getenv("DART_API_KEY")
        if dart_key:
            dart = DartConnector(api_key=dart_key)
            await dart.connect()
            logger.info("DART connector initialized")
        else:
            logger.warning("DART_API_KEY missing → financial data excluded")
        news = NewsCrawler()
        await news.connect()
        logger.info("News crawler initialized")

    async def start_performance_tracker(self) -> None:
        """PerformanceTracker 시작 + BanditFeedbackBridge 연결 (v3.0)."""
        if not self.db:
            return
        performance_tracker.initialize(self.db)

        # ─── StrategyBandit 초기화 ────────────────────────────────
        self.bandit = StrategyBandit(
            strategy_names=["Trend", "Reversal", "Breakout"],
            decay=0.99,
        )
        logger.info("StrategyBandit initialized (arms: Trend/Reversal/Breakout)")

        # ─── BanditFeedbackBridge 연결 ────────────────────────────
        self.bandit_bridge = BanditFeedbackBridge(
            db=self.db,
            bandit=self.bandit,
            feedback_days=7,
        )
        performance_tracker.attach_bandit_bridge(self.bandit_bridge)
        logger.info("BanditFeedbackBridge attached to PerformanceTracker")

        await performance_tracker.start()
        logger.info("PerformanceTracker v3.0 started (5min update loop + Bandit feedback)")

    async def init_execution(self) -> None:
        """OrderExecutor / Calibrator 초기화."""
        if not self.container:
            raise RuntimeError("Container missing")
        _ = self.container.order_executor  # Paper Mode 초기화
        logger.info("OrderExecutor initialized (Paper Mode)")
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

        # ── 시그니처: add_job_with_retry(coro_func, trigger, job_id, *args, ...)
        # trigger → job_id → *args(함수 인자) 순서 준수
        self.scheduler.add_job_with_retry(
            self._run_daily_report,
            CronTrigger(hour=sched.daily_report_hour, minute=sched.daily_report_minute, timezone="Asia/Seoul"),
            "daily_report",
            daily_reporter,                      # *args → _run_daily_report(reporter)
            max_retries=3, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_feedback_learning,
            CronTrigger(hour=sched.feedback_hour, minute=sched.feedback_minute, timezone="Asia/Seoul"),
            "feedback_learning",
            feedback_learner,                    # *args → _run_feedback_learning(learner)
            max_retries=3, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_weekly_pdf,
            CronTrigger(day_of_week=sched.weekly_pdf_day, hour=sched.weekly_pdf_hour, minute=sched.weekly_pdf_minute, timezone="Asia/Seoul"),
            "weekly_pdf",
            weekly_pdf_gen,                      # *args → _run_weekly_pdf(pdf_gen)
            max_retries=3, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_daily_ohlcv,
            CronTrigger(hour=sched.ohlcv_hour, minute=sched.ohlcv_minute, timezone="Asia/Seoul"),
            "daily_ohlcv",                       # *args 없음 → _run_daily_ohlcv()
            max_retries=3, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_macro_update,
            CronTrigger(hour=sched.macro_update_hour, minute=sched.macro_update_minute, timezone="Asia/Seoul"),
            "macro_update",                      # *args 없음 → _run_macro_update()
            max_retries=3, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_phase_transition_check,
            CronTrigger(hour=17, minute=30, timezone="Asia/Seoul"),
            "phase_transition_check",
            sender,                              # *args → _run_phase_transition_check(sender)
            max_retries=2, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_calibration,
            CronTrigger(hour=17, minute=30, timezone="Asia/Seoul"),
            "calibration",
            calibrator,                          # *args → _run_calibration(calibrator)
            max_retries=2, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            scheduled_verify,
            CronTrigger(hour=16, minute=0, timezone="Asia/Seoul"),
            "alert_verifier",                    # *args 없음 → scheduled_verify()
            max_retries=2, retry_delay=5,
        )
        self.scheduler.start()
        self.startup_details["job_count"] = 8
        log_event("SCHEDULER_STARTED", {"jobs": 8})
        logger.info("Scheduler started (8 jobs registered)")

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

        status = {
            "status": "healthy" if (queue_usage < 90 and data_flow_ok) else "degraded",
            "uptime_seconds": time.time() - self.start_time if self.start_time else 0,
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
            },
            "blackbox": bb_get_status(),
            "regime": regime_manager.get_status().get("current_regime", "Sideways"),
            "macro": get_cached_macro().to_dict(),
            "performance": performance_tracker.get_status() if performance_tracker else {},
            "collector": collector_status.get_summary(),
            "workers": {
                "total": len(self.worker_tasks),
                "alive": sum(1 for t in self.worker_tasks if not t.done()),
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
    #  5. 전략 Worker (V10 SignalPipeline 통합)
    # ═══════════════════════════════════════════════════════════════

    async def _strategy_worker(
        self,
        wid: int,
        analyzer: DeepAnalyzer,
        db: DatabaseManager,
        sender: TelegramSender,
    ) -> None:
        """전략 분석 Worker.

        메시지 큐에서 틱 데이터를 꺼내 DeepAnalyzer로 분석합니다.
        V10: SignalPipeline을 통해 분석 결과를 보강합니다.
        """
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
                    # ─── DeepAnalyzer 분석 ────────────────────────
                    analysis = await analyzer.analyze(stock_data)

                    # ─── V10: SignalPipeline 보강 ─────────────────
                    # SignalPipeline은 기술 지표 기반 신뢰도 검증 역할
                    if (
                        self.signal_pipeline
                        and analysis.get("action") not in ("ERROR", "EVENT_EXIT")
                    ):
                        try:
                            v10_signal = await self.signal_pipeline.process(stock_data)
                            # SQI 낮으면 분석 결과에 경고 태그 추가
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
    #  6. 메인 루프
    # ═══════════════════════════════════════════════════════════════

    async def run_main_loop(self) -> None:
        """메인 스캔 루프 - 실시간 시장 데이터 폴링."""
        logger.info("Main loop started (V10)")
        log_event("MAIN_LOOP_START", {})
        self.start_time = time.time()

        while not self._shutdown_requested and not self._shutdown_event.is_set():
            try:
                # ─── SafetyGuard 점검 ────────────────────────────
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
                    if safety_result.get("action") == "BLOCK_ALL":
                        triggered = safety_result.get("triggered", [])
                        logger.critical(f"SafetyGuard triggered: {triggered}")
                        await self._send_error_alert(
                            "SafetyGuard 차단 활성화",
                            str([t.get("condition") for t in triggered])[:200],
                        )
                        await asyncio.sleep(10)
                        continue

                # ─── Kiwoom 연결 확인 ─────────────────────────────
                if not self.kiwoom.is_connected():
                    await self._reconnect()
                    await asyncio.sleep(1)
                    continue

                # ─── 데이터흐름 타임아웃 감시 ──────────────────────
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

                # ─── 스캔 & 큐 적재 ───────────────────────────────
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
            msg = (
                f"{status_emoji} <b>V10 시스템 시작 성공</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"🤖 PID: {os.getpid()}\n"
                f"📡 구독 종목: {len(tickers)}개 → {ticker_str}\n"
                f"🔌 키움: {'✅ 연결됨' if details.get('kiwoom_connected') else '❌'}\n"
                f"⏰ 스케줄러: {details.get('job_count', 0)}개\n"
                f"📊 KOSPI 5일: {macro.kospi_trend:.2f}%  |  USD/KRW: {macro.usdkrw:.0f}"
                f"  |  VIX: {macro.vix:.1f}\n"
                f"💾 블랙박스: {bb['file_count']}개  {bb['total_size_mb']}MB\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>V10 DDD + SignalPipeline + MAB 앙상블 활성화</i>"
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

        return {
            "status": "운영 중" if (self.kiwoom and self.kiwoom.is_connected()) else "연결 끊김",
            "uptime_seconds": time.time() - self.start_time if self.start_time else 0,
            "tickers": self.monitor.get_subscribed_count() if self.monitor else 0,
            "last_data_ago": last_ago,
            "kiwoom_connected": self.kiwoom.is_connected() if self.kiwoom else False,
            "queue_usage": queue_usage,
            "worker_status": f"{alive_workers}/{len(self.worker_tasks)} 활성",
            "signal_pipeline": "V10 활성" if self.signal_pipeline else "비활성",
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
        }

    # ═══════════════════════════════════════════════════════════════
    #  10. 부트스트랩 메인 진입점
    # ═══════════════════════════════════════════════════════════════

    async def bootstrap(self, shutdown_event: Optional[asyncio.Event] = None) -> None:
        """전체 시스템 부트스트랩 시퀀스.

        app/main.py에서 호출되는 유일한 public 메서드.
        """
        self._shutdown_event = shutdown_event or asyncio.Event()
        startup_success = False

        try:
            # ─── 사전 검증 ────────────────────────────────────────
            self.load_env()
            self.validate_env()
            self.manage_pid()

            # ─── 전역 예외 핸들러 ─────────────────────────────────
            self._original_exception_handlers = setup_global_exception_handler()
            log_event("SYSTEM_START", {"pid": os.getpid(), "version": "V10"})
            debug_tower.log("SYSTEM", "MAIN_START", {"pid": os.getpid()})

            collector_status.register("system", freshness_seconds=None)

            # ─── 핵심 컴포넌트 순서대로 초기화 ──────────────────
            logger.info("=" * 60)
            logger.info("V10 System Bootstrap Starting...")
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
            await self.init_analyzer()          # DeepAnalyzer + SignalPipeline
            await self.init_data_sources()
            await self.start_performance_tracker()
            await self.init_execution()
            await self.start_telegram_commands()
            await self.init_scheduler()
            await self.start_workers()

            # ─── 헬스체크 서버 ────────────────────────────────────
            health_task = asyncio.create_task(self.start_health_server())
            self.all_tasks.append(health_task)

            # ─── PerformanceTracker 태스크 등록 ──────────────────
            if hasattr(performance_tracker, "_task") and performance_tracker._task:
                self.all_tasks.append(performance_tracker._task)

            self.safety_guard = SafetyGuard()
            startup_success = True

            logger.info("=" * 60)
            logger.info("V10 System Bootstrap Complete")
            logger.info(f"  Tickers: {self.startup_details.get('ticker_count', 0)}")
            logger.info(f"  Scheduler jobs: {self.startup_details.get('job_count', 0)}")
            logger.info(f"  SignalPipeline: {'Active' if self.signal_pipeline else 'Inactive'}")
            logger.info("=" * 60)

            log_event("SYSTEM_READY", {})
            debug_tower.log("SYSTEM", "SYSTEM_READY", {})
            await self._send_startup_notification(True)

            # ─── 메인 루프 ────────────────────────────────────────
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
        """정상 종료 시퀀스 (역순 정리)."""
        self._shutdown_requested = True
        log_event("SYSTEM_SHUTDOWN", {})
        debug_tower.log("SYSTEM", "SYSTEM_SHUTDOWN", {})

        # ─── trailing_stops DB 저장 ───────────────────────────────
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

        # ─── Worker 태스크 취소 ───────────────────────────────────
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

        # ─── PortfolioManager 종료 ────────────────────────────────
        if self.analyzer and hasattr(self.analyzer, "portfolio_manager"):
            await self.analyzer.portfolio_manager.stop()

        # ─── 컴포넌트 역순 종료 ───────────────────────────────────
        if self.telegram_cmd:
            await self.telegram_cmd.stop()
        await regime_manager.stop()
        if self.kiwoom:
            await self.kiwoom.disconnect()
        if self.scheduler:
            self.scheduler.shutdown()
        await performance_tracker.stop()
        if self.container:
            await self.container.shutdown()

        # ─── CollectorStatus 요약 ──────────────────────────────────
        try:
            summary = collector_status.get_summary()
            logger.info(
                f"Collector summary: healthy={summary['healthy']}/{summary['total']}"
            )
        except Exception:
            pass

        # ─── 전역 예외 핸들러 복원 ────────────────────────────────
        if self._original_exception_handlers:
            try:
                restore_exception_handler(self._original_exception_handlers)
            except Exception:
                pass

        # ─── PID 파일 / DebugTower 정리 ──────────────────────────
        self.cleanup_pid()
        debug_tower.flush()

        logger.info("System shutdown complete")
