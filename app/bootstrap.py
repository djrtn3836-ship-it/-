# -*- coding: utf-8 -*-
"""
app/bootstrap.py - V10 DI Container and Boot Sequence v2.5.1

v2.5.1 변경 (Session 41: mypy strict 적용):
    - 모든 메서드 반환 타입 및 파라미터 타입 명시
    - message_queue, worker_tasks, all_tasks 등 내부 자료구조 제네릭 타입 명시
    - self.db/self.kiwoom/self.monitor/self._shutdown_event가 Optional로
      선언되어 있어 strict 모드에서 요구하는 None 가드를 필요한 지점에 추가.
      정상 부트스트랩 흐름에서는 이 속성들이 실제로 None이 되지 않으므로
      런타임 동작에 실질적 영향 없음(안전망 성격).
    - 유일한 예외: init_scheduler()에서 self.db가 없으면 calibrator를 생성하지
      않고 해당 스케줄 작업 등록도 건너뛰도록 함(정상 흐름에서 self.db는
      항상 non-None이라 실질적 차이는 없으나, 미세한 로직 변경이므로 명시함).
    - ⚠️ 이 파일은 strict 목록 등록 직후 단독 mypy 실행으로 전체 오류를
      재확인해야 함(Select-Object -First 15의 잘린 목록만으로는 이 파일의
      전체 오류 개수를 확정할 수 없음 - Session 33의 config/schema.py
      사례와 동일한 방법론 적용 필요).
"""

import asyncio
import logging
import os
import subprocess
import sys
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from observability.tracer import get_tracer
from observability.auto_trace import TracedService
from observability.trace_id import bind_trace_id, reset_trace_id, new_trace_id
from observability.health_score import calculate_health_score

trace = get_tracer(__name__)

from config.schema import get_config
from core.container import AppContainer
from core.logger import setup_logger
from core.exception_handler import set_alert_handler
from core.runtime_mode import get_runtime_mode, load_runtime_mode
from core.holiday_utils import is_trading_day
from core.regime_manager import regime_manager
from core.scheduler import SchedulerManager

from core.blackbox_logger import get_status as bb_get_status, log_error, log_event
from core.debug_tower import debug_tower
from core.exception_handler import (
    restore_exception_handler,
    setup_global_exception_handler,
)
from core.supervisor import SystemSupervisor

from data.db_manager import DatabaseManager

try:
    from infrastructure.dart.client import DartConnector
except ImportError:
    from data.dart_connector import DartConnector  # type: ignore

try:
    from infrastructure.news.crawler import NewsCrawler
except ImportError:
    from data.news_crawler import NewsCrawler  # type: ignore[assignment]

# 키움 연동: 정식 경로(infrastructure.kiwoom) = data/scanner 원본의 재수출 shim.
#   과거엔 infrastructure/kiwoom/ 폴더가 없어 try가 항상 실패 → 폴백이 조용히 실행됐다(P12-1).
#   폴백은 다른 환경 대비 안전망으로만 남겨둔다(사용 시 DEBUG 로그).
try:
    from infrastructure.kiwoom import KiwoomConnectorV512
except ImportError:                                    # pragma: no cover - 안전망
    logging.getLogger("bootstrap").warning(
        "infrastructure.kiwoom 사용 불가 → data.kiwoom_connector 폴백"
    )
    from data.kiwoom_connector import KiwoomConnectorV512

try:
    from infrastructure.kiwoom.monitor import RealtimeMonitor
except ImportError:                                    # pragma: no cover - 안전망
    logging.getLogger("bootstrap").warning(
        "infrastructure.kiwoom.monitor 사용 불가 → scanner 폴백"
    )
    from scanner.realtime_monitor import RealtimeMonitor

from scanner.deep_analyzer import DeepAnalyzer

from application.analysis.signal_pipeline import SignalPipeline
from application.analysis.strategy_bandit import StrategyBandit
from application.analysis.bandit_feedback_bridge import BanditFeedbackBridge
from application.analysis.ab_framework import get_ab_manager, ABTestManager
from application.analysis.tuning_executor import TuningExecutor, load_tuning_state

from orchestrator.sentiment_pipeline import SentimentPipeline
from orchestrator.portfolio_manager import PortfolioManager

from analytics.performance_tracker import performance_tracker
from analytics.paper_trader import PaperTrader
from analytics.calibration_executor import ExecutionCalibrator
from analytics.alert_verifier import scheduled_verify
from scheduler.momentum_report import send_momentum_report
from report.html_dashboard import scheduled_dashboard
from risk.correlation_monitor import scheduled_correlation_check
from analytics.calibration_bridge import settle_and_feed_ab
from report.telegram_sender import TelegramSender
from report.telegram_commands import TelegramCommandHandler
from report.daily_report import DailyReportGenerator
from report.weekly_pdf import WeeklyPDFGenerator
from observability.ops_monitor import OpsMonitor
from risk.market_risk_monitor import cb_input_provider, scheduled_market_risk_check
from scheduler.data_readiness_monitor import scheduled_data_readiness_check
from scheduler.universe_fetcher import scheduled_universe_refresh
from core.db_backup import backup_sqlite, last_backup_age_hours
from core.disk_guard import check_disk
from core.db_maintenance import maintain_sqlite
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

_DATA_FLOW_TIMEOUT: int = 180
_REQUIRED_ENV_KEYS: List[str] = [
    "KIWOOM_APP_KEY",
    "KIWOOM_APP_SECRET",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]
PID_FILE: Path = Path(__file__).parent.parent / "scanner.pid"


class Bootstrapper(TracedService):
    """V10 부트스트래퍼 - 시스템의 유일한 진입점."""

    def __init__(self) -> None:
        self._shutdown_requested: bool = False
        self._shutdown_event: Optional[asyncio.Event] = None

        self.container: Optional[AppContainer] = None
        self.kiwoom: Optional[KiwoomConnectorV512] = None
        self.monitor: Optional[RealtimeMonitor] = None
        self.db: Optional[DatabaseManager] = None
        self.scheduler: Optional[SchedulerManager] = None
        self.telegram_cmd: Optional[TelegramCommandHandler] = None
        self.safety_guard: Optional[SafetyGuard] = None
        self.analyzer: Optional[DeepAnalyzer] = None
        self.signal_pipeline: Optional[SignalPipeline] = None
        self.ops_monitor: Optional[OpsMonitor] = None
        # 모의매매 측정(2026-10-10): 신호 기록 → 실현가 채점 → 정직한 승률
        self.paper_trader: Optional[PaperTrader] = None
        # 알림 정밀도(2026-10-10): 같은 종목·행동 반복 알림 억제
        self._alert_last_at: Dict[str, float] = {}
        self._alert_cooldown_sec: float = float(os.getenv("ALERT_COOLDOWN_SEC", "1800"))
        self.bandit: Optional[StrategyBandit] = None
        self.bandit_bridge: Optional[BanditFeedbackBridge] = None
        self.ab_manager: Optional[ABTestManager] = None
        self.tuning_executor: Optional[TuningExecutor] = None
        self.sentiment_pipeline: Optional[SentimentPipeline] = None
        self.news_crawler: Optional[Any] = None
        self.dart_connector: Optional[Any] = None
        self._error_sender: Optional[TelegramSender] = None
        self._original_exception_handlers: Optional[Dict[str, Any]] = None

        # 🔴 2026-10-10 알림 신뢰성: 전송을 워커에서 분리한다.
        #   (a) 네트워크 장애 시 send_raw 재시도(≈3분)로 워커 2개가 동시에 멈추던 문제
        #   (b) 전송 실패 알림이 로그만 남기고 영구 유실되던 문제
        self._alert_queue: "asyncio.Queue[Tuple[str, Dict[str, Any]]]" = asyncio.Queue(
            maxsize=int(os.getenv("ALERT_QUEUE_MAX", "1000"))
        )
        self._alert_task: Optional["asyncio.Task[Any]"] = None
        self._alert_backlog: List[Dict[str, Any]] = []
        self._alert_backlog_path = Path(
            os.getenv("ALERT_BACKLOG_PATH", "logs/pending_alerts.jsonl")
        )
        self._alert_backlog_max = int(os.getenv("ALERT_BACKLOG_MAX", "200"))
        self._alert_backlog_last_try: float = 0.0
        # 디스크 여유 부족 알림 쿨다운(기본 6시간) — 도배 방지
        self._disk_alert_last_at: float = 0.0
        self._disk_alert_cooldown_sec: float = float(
            os.getenv("DISK_ALERT_COOLDOWN_SEC", "21600")
        )

        self.message_queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(
            maxsize=config.queue_maxsize
        )
        self.worker_tasks: List["asyncio.Task[Any]"] = []
        self.all_tasks: List["asyncio.Task[Any]"] = []

        self.start_time: float = 0.0
        self._last_data_time: float = 0.0
        # 🔴 2026-10-08 사고: 틱마다 심층 분석 → 하루 decisions 285만 건 / DB +1.5GB /
        #   로그 +2.2GB / 메모리 7.3GB. → 종목당 일정 간격으로만 분석한다.
        self._analysis_interval_sec: float = float(os.getenv("ANALYSIS_MIN_INTERVAL_SEC", "300"))
        self._analysis_last_at: Dict[str, float] = {}
        # 급변(기본 |변동률| >= 2%)은 알림 지연 방지를 위해 즉시 분석
        self._signal_bypass_pct: float = float(os.getenv("ANALYSIS_BYPASS_PCT", "2.0"))
        self.startup_details: Dict[str, Any] = {}

    def load_env(self) -> None:
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=True)
            logger.info(f".env loaded: {env_path}")
        self._normalize_env_aliases()

    @staticmethod
    def _normalize_env_aliases() -> None:
        """구버전 env 변수명을 표준명으로 정규화한다 (하위 호환).

        .env / .env.example / 코드 간 네이밍 불일치로 부팅이 실패하는 문제를
        방지한다. 표준명이 이미 설정되어 있으면 그대로 둔다(덮어쓰지 않음).
        """
        aliases: Dict[str, tuple] = {
            "KIWOOM_APP_KEY": ("KIWOOM_API_KEY",),
            "KIWOOM_APP_SECRET": ("KIWOOM_SECRET_KEY", "KIWOOM_API_SECRET"),
        }
        for canonical, legacy_names in aliases.items():
            if os.getenv(canonical):
                continue
            for legacy in legacy_names:
                value = os.getenv(legacy)
                if value:
                    os.environ[canonical] = value
                    logger.warning(
                        f"env 별칭 정규화: {legacy} → {canonical} "
                        f"(.env 변수명을 {canonical}로 정정 권장)"
                    )
                    break

    def validate_env(self) -> None:
        missing = [k for k in _REQUIRED_ENV_KEYS if not os.getenv(k)]
        if missing:
            if get_runtime_mode().test_mode:
                logger.warning(
                    f"🧪 [SAFE MODE] 필수 환경변수 누락({', '.join(missing)}) — "
                    f"TEST_MODE이므로 부팅을 계속합니다(외부 연결 없음)"
                )
                debug_tower.log("SYSTEM", "ENV_VALIDATED_TEST_MODE", {"missing": missing})
                return
            msg = f"필수 환경변수 누락: {', '.join(missing)}"
            logger.critical(msg)
            print(f"[ERROR] {msg}")
            sys.exit(1)
        logger.info("환경변수 검증 완료")
        debug_tower.log("SYSTEM", "ENV_VALIDATED", {})

    def _is_process_alive(self, pid: int) -> bool:
        """PID가 실제 살아있는 python 프로세스인지 확인한다(PID 재사용 오탐 방지)."""
        try:
            if sys.platform == "win32":
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True,
                )
                out = (result.stdout or "").strip()
                if not out or str(pid) not in out:
                    return False
                return "python" in out.lower()
            return Path(f"/proc/{pid}").exists()
        except Exception:
            return False

    def manage_pid(self) -> None:
        """단일 인스턴스 보장: 살아있는 인스턴스가 있으면 중복 실행을 차단한다.

        🔴 2026-10-12 사고: PID 파일 가드가 뚫려 4개 인스턴스가 동시 실행
           (같은 봇 폴링 Conflict + SQLite lock → 오류 텔레그램 폭주).
           → 포트 소켓 락을 1차 방어로 추가. CWD·PID 재사용·권한 문제에
             영향받지 않아 가장 확실하다(포트는 시스템 전역 자원).
        """
        # 인스턴스 락(포트 47653)은 app/main.py 최상단에서 획득한다(더 이른 방어).
        my_pid = os.getpid()
        if PID_FILE.exists():
            try:
                old_pid = int((PID_FILE.read_text() or "0").strip() or "0")
            except Exception:
                old_pid = 0

            if old_pid and old_pid != my_pid and self._is_process_alive(old_pid):
                msg = f"이미 실행 중인 인스턴스 발견 (PID: {old_pid}) : 중복 실행을 중단합니다"
                logger.critical(msg)
                print(f"[ERROR] {msg}")
                sys.exit(1)

            try:
                PID_FILE.unlink()
                logger.info(f"스테일 PID 파일 정리 (이전 PID {old_pid})")
            except Exception:
                pass

        PID_FILE.write_text(str(my_pid))
        logger.info(f"PID 파일 생성: {my_pid}")

    @staticmethod
    def _in_data_flow_window(now_dt: datetime) -> bool:
        """데이터 흐름 감시 창 = 거래일 09:00~15:20.

        🔴 2026-10-09 사고: 거래일 검사 없이 시간대만 봐서 휴장일(한글날)에도
           3분마다 강제 재연결(disconnect→connect→resubscribe) + ERROR 로그
           → kiwoom_rest.log 4.2MB + bootstrap 오류 도배.
        """
        if not is_trading_day(now_dt):
            return False
        return 9 <= now_dt.hour <= 15 and not (now_dt.hour == 15 and now_dt.minute >= 20)

    def _should_analyze(self, ticker: str, change_abs: float, now: float) -> bool:
        """종목당 심층 분석 간격 제한.

        - 급변(|변동률| >= ANALYSIS_BYPASS_PCT, 기본 2%)은 즉시 분석(알림 지연 방지)
        - 그 외에는 ANALYSIS_MIN_INTERVAL_SEC(기본 300초)에 1회만 분석
        2026-10-08 사고: 틱마다 분석 → decisions 285만 건/일, DB +1.5GB, 로그 +2.2GB, 메모리 7.3GB.
        """
        if change_abs >= self._signal_bypass_pct:
            return True
        return (now - self._analysis_last_at.get(ticker, 0.0)) >= self._analysis_interval_sec

    def cleanup_pid(self) -> None:
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass

    async def init_container(self) -> None:
        self.container = AppContainer.create_production()
        await self.container.initialize()
        self.db = self.container.db_manager
        self.kiwoom = self.container.kiwoom
        log_event("CONTAINER_INITIALIZED", {})
        logger.info("DI container initialized")

    async def init_telegram(self) -> None:
        self._error_sender = TelegramSender()
        set_alert_callback(self._send_error_alert)
        set_alert_handler(self._send_error_alert)
        logger.info("Telegram alert handler connected")

    async def connect_kiwoom(self) -> None:
        if get_runtime_mode().test_mode:
            from infrastructure.market_data.mock_kiwoom_connector import MockKiwoomConnector

            mock = MockKiwoomConnector()
            await mock.connect()
            self.kiwoom = mock
            if self.container is not None:
                try:
                    self.container.kiwoom = mock
                except Exception:
                    pass
            logger.warning("🧪 [SAFE MODE] 실제 키움 연결 생략 — 모의 커넥터로 대체")
            log_event("KIWOOM_MOCK_CONNECTED", {})
            return

        if not self.kiwoom:
            raise RuntimeError("Kiwoom connector missing")
        logger.info("Waiting for Kiwoom connection...")
        ws = config.websocket
        max_attempts = ws.reconnect_max_attempts
        base_delay = ws.reconnect_base_delay
        max_delay = ws.reconnect_max_delay
        retry_count = 0
        while not self.kiwoom.is_connected():
            if retry_count >= max_attempts:
                msg = (
                    f"Kiwoom 연결 실패: 최대 재시도({max_attempts}회) 초과 → 시작 중단 "
                    f"(자격증명/네트워크/방화벽 확인 필요)"
                )
                logger.critical(msg)
                log_event("KIWOOM_CONNECT_FAILED", {"attempts": retry_count})
                debug_tower.log("SYSTEM", "KIWOOM_CONNECT_FAILED", {"attempts": retry_count})
                await self._send_error_alert(msg)
                raise RuntimeError(msg)
            retry_count += 1
            await self.kiwoom.connect()
            if self.kiwoom.is_connected():
                break
            delay = min(base_delay * (2 ** (retry_count - 1)), max_delay)
            logger.warning(
                f"⚠️ Kiwoom 연결 실패 ({retry_count}/{max_attempts}) → {delay}초 후 재시도"
            )
            debug_tower.log("SYSTEM", "KIWOOM_RETRY", {"attempt": retry_count, "delay": delay})
            await asyncio.sleep(delay)
        logger.info(f"Kiwoom connected (retries={retry_count})")
        log_event("KIWOOM_CONNECTED", {"retries": retry_count})

        if not await self.kiwoom.wait_until_ready(timeout=10.0):
            await self.kiwoom.disconnect()
            await self.kiwoom.connect()
            if not await self.kiwoom.wait_until_ready(timeout=10.0):
                raise RuntimeError("WebSocket timeout after reconnect")
        logger.info("WebSocket ready")
        debug_tower.log("SYSTEM", "WS_READY_OK", {})

    async def _send_ops_alert(self, message: str) -> None:
        """OpsMonitor 이상 알림을 텔레그램으로 전송한다(안전모드는 sender가 차단)."""
        try:
            await TelegramSender().send_raw(message)
        except Exception as e:
            logger.warning(f"OpsMonitor 알림 전송 실패: {e}")

    @staticmethod
    def _in_signal_window(now_dt: Optional[datetime] = None) -> bool:
        """정규장 신호 창 = 거래일 09:00~15:35 (KST).

        🔴 2026-10-14 장외소음: 시간외단일가(~18:00) 틱이 워커에서 계속 분석돼
           포지션 재평가/신호 이벤트가 장 마감 후에도 쌓였다(실측 16:32~18:31).
           → 정규장 외 틱은 분석·기록·모의매매 전부 제외(알림 차단은 별도 유지).
        15:35 유예: 15:30 동시호가 종료 직후 확정 틱 1회는 허용.
        """
        now = now_dt or datetime.now()
        try:
            from core.holiday_utils import is_trading_day

            if not is_trading_day(now):
                return False
        except Exception:
            return True  # 판단 불가 시 가용성 우선
        minutes = now.hour * 60 + now.minute
        return 9 * 60 <= minutes <= 15 * 60 + 35

    @staticmethod
    def _alert_hours_ok() -> bool:
        """신호 알림 허용 시간 = 거래일 09:00~15:29 (KST). 기록·모의매매와 무관."""
        try:
            from core.holiday_utils import is_market_open

            return bool(is_market_open())
        except Exception:
            return True  # 확인 불가 시 알림 허용(가용성 우선)

    def _should_send_alert(self, ticker: str, action: str, now: Optional[float] = None) -> bool:
        """같은 종목·행동의 반복 알림을 억제한다 (기본 30분).

        호출 시각을 기록하므로 True를 반환한 호출만 실제 발송된다.
        """
        key = f"{ticker}:{action}"
        _now = time.time() if now is None else now
        last = self._alert_last_at.get(key)
        # 첫 알림(기록 없음)은 항상 발송 — 시계 값이 작은 환경(테스트 등)에서도 동작
        if last is not None and (_now - last) < self._alert_cooldown_sec:
            return False
        self._alert_last_at[key] = _now
        return True

    async def _send_briefing(self, kind: str) -> None:
        """종합 브리핑 발송(아침/저녁). 실패해도 조용히 넘어간다."""
        try:
            from report.briefing import build_briefing

            text = await build_briefing(self.db, kind=kind, paper_trader=self.paper_trader)
            self._enqueue_admin_message(text)
            debug_tower.log("SYSTEM", "BRIEFING_SENT", {"kind": kind})
        except Exception as e:
            logger.warning(f"브리핑 생성 실패(무시): {e}")

    async def _run_morning_briefing(self) -> None:
        await self._send_briefing("morning")

    async def _run_evening_briefing(self) -> None:
        await self._send_briefing("evening")

    async def _catchup_daily_jobs(self) -> None:
        """기동 시 놓친 일일 잡 자가 복구 (2026-10-15 재부팅/늦은 기동 사고).

        - 정상 08:40 기동 시: 아무 것도 하지 않음(임계 이전)
        - 16:40 이후 기동 + 오늘 일봉 부재 → 수집
        - 16:45 이후 기동 → 수급 재수집(멱등 upsert)
        - 17:10 이후 기동 + 오늘 채점 기록 부재 → 정산+리포트 1회(중복 발송 방지)
        """
        try:
            await asyncio.sleep(45)  # 기동 안정화 후
            try:
                if not is_trading_day():
                    return
            except Exception:
                pass
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            mins = now.hour * 60 + now.minute

            if mins >= 16 * 60 + 40:
                has_today = False
                try:
                    rows = await self.db.get_ohlcv("005930", 5)
                    has_today = any(str(r.get("date", ""))[:10] == today for r in (rows or []))
                except Exception:
                    has_today = True  # 확인 불가 시 스킵(오버런 방지)
                if not has_today:
                    logger.info("⏪ 놓친 일봉 수집 감지 → 자가 복구 실행")
                    try:
                        await self._run_daily_ohlcv()
                        await self.db._flush_pending()
                    except Exception as e:
                        logger.warning(f"일봉 자가 복구 실패(무시): {e}")

            if mins >= 16 * 60 + 45:
                try:
                    logger.info("⏪ 수급 자가 복구(멱등) 실행")
                    await self._run_supply_demand()
                except Exception as e:
                    logger.warning(f"수급 자가 복구 실패(무시): {e}")

            if mins >= 17 * 60 + 10:
                settled_today = False
                try:
                    from analytics.paper_trader import SETTLED_PATH

                    if SETTLED_PATH.exists():
                        lines = [l for l in SETTLED_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
                        if lines:
                            import json as _json

                            settled_today = str(
                                _json.loads(lines[-1]).get("settled_at", "")
                            ).startswith(today)
                except Exception:
                    settled_today = True  # 확인 불가 시 스킵(중복 리포트 방지)
                if not settled_today:
                    logger.info("⏪ 놓친 모의매매 정산 감지 → 자가 복구 실행")
                    try:
                        await self._run_paper_settle()
                    except Exception as e:
                        logger.warning(f"정산 자가 복구 실패(무시): {e}")
        except Exception as e:
            logger.debug(f"자가 복구 스킵: {e}")

    async def _run_supply_demand(self) -> None:
        """일별 수급(외국인/기관) 수집 — 판단에는 아직 미반영(측정용 태그만)."""
        if not is_trading_day() or not self.kiwoom or not self.db or not self.monitor:
            return
        try:
            from collector.supply_demand_collector import collect_supply_demand

            saved = await collect_supply_demand(
                self.kiwoom,
                self.db,
                self.monitor.tickers,
                date_str=datetime.now().strftime("%Y-%m-%d"),
            )
            debug_tower.log("SYSTEM", "SUPPLY_DEMAND_COLLECTED", {"saved": saved})
        except Exception as e:
            logger.warning(f"수급 수집 실패(무시): {e}")

    async def _run_disk_check(self) -> None:
        """디스크 여유 확인. 부족하면 쿨다운 내 1회 알림(성공은 조용히)."""
        try:
            warn = await asyncio.to_thread(check_disk)
        except Exception as e:
            logger.debug(f"디스크 검사 실패(무시): {e}")
            return
        if not warn:
            return
        now = time.time()
        if now - self._disk_alert_last_at < self._disk_alert_cooldown_sec:
            logger.warning("디스크 여유 부족(알림 쿨다운 중)")
            return
        self._disk_alert_last_at = now
        logger.error("디스크 여유 부족 → 알림 발송")
        debug_tower.log("SYSTEM", "DISK_LOW", {})
        self._enqueue_admin_message(warn)

    async def _run_db_maintenance(self) -> None:
        """주간 SQLite 유지보수(PRAGMA optimize) + 통계 로그. 실패 시 알림."""
        try:
            r = await asyncio.to_thread(maintain_sqlite)
        except Exception as e:
            logger.error(f"DB 유지보수 실패: {e}")
            self._enqueue_admin_message(
                f"🧰 <b>DB 유지보수 실패</b>\n📌 {str(e)[:200]}"
            )
            return
        if r.get("status") != "ok":
            logger.warning(f"DB 유지보수 건너뜀: {r}")
            return
        top = sorted(r["tables"].items(), key=lambda kv: kv[1], reverse=True)[:5]
        summary = ", ".join(f"{k}={v:,}" for k, v in top)
        logger.info(
            f"DB 유지보수 완료: {r['size_mb']}MB 무결성 {r['integrity']} | {summary}"
        )
        debug_tower.log("SYSTEM", "DB_MAINTENANCE_OK", {"size_mb": r["size_mb"]})

    async def _run_db_backup(self) -> None:
        """DB 스냅샷 백업 + 무결성 검사. 실패하면 알림(성공은 조용히)."""
        try:
            path = await asyncio.to_thread(backup_sqlite)
            if path is None:
                logger.warning("DB 백업 건너뜀(원본 DB 없음)")
                return
            size_mb = path.stat().st_size / 1048576
            logger.info(f"DB 백업 완료: {path.name} ({size_mb:.1f}MB)")
            debug_tower.log("SYSTEM", "DB_BACKUP_OK", {"file": path.name})
        except Exception as e:
            logger.error(f"DB 백업 실패: {e}")
            debug_tower.log("SYSTEM", "DB_BACKUP_FAILED", {"error": str(e)[:200]})
            self._enqueue_admin_message(
                f"🗄️ <b>DB 백업 실패</b>\n📌 {str(e)[:200]}"
            )

    def _backup_if_stale(self) -> None:
        """기동 시 마지막 백업이 24시간보다 오래됐으면 즉시 1회 백업한다."""
        try:
            age = last_backup_age_hours()
            if age is None or age >= 24.0:
                task = asyncio.create_task(self._run_db_backup())
                self.all_tasks.append(task)
                logger.info("백업이 없거나 오래됨 → 기동 백업 실행")
        except Exception as e:
            logger.debug(f"백업 신선도 검사 실패(무시): {e}")

    def _enqueue_admin_message(self, text: str) -> bool:
        """관리 알림을 큐로 보낸다(워커 비블로킹). 큐 미가동이면 직접 발송 시도."""
        try:
            if self._alert_task is not None and not self._alert_task.done():
                return self._enqueue_alert(
                    text, {"action": "RAW", "ticker": "SYSTEM", "text": text}
                )
        except Exception:
            pass
        try:
            sender = self._error_sender or TelegramSender()
            asyncio.create_task(sender.send_raw(text))
            return True
        except Exception:
            return False

    async def _run_paper_settle(self) -> None:
        """모의매매 채점 + 성과 리포트 전송(평일 16:10).

        실현 종가로만 채점하므로 승률을 과장할 여지가 없다.
        """
        if self.paper_trader is None:
            return
        try:
            settled = await self.paper_trader.settle_pending()
            report = self.paper_trader.format_report(days=90)
            logger.info(f"모의매매 채점 완료: {settled}건 확정")
            await TelegramSender().send_raw(report)
        except Exception as e:
            logger.warning(f"모의매매 리포트 실패(무시): {e}")

    def get_ops_snapshot(self) -> Optional[dict]:
        """관측 상태 스냅샷(헬스/명령어 응답용)."""
        return self.ops_monitor.snapshot() if self.ops_monitor else None

    def _get_realtime_price(self, ticker: str) -> float:
        if self.monitor is None:
            return 0.0
        try:
            price = self.monitor.get_latest_price(ticker)
            return float(price) if price else 0.0
        except Exception:
            return 0.0

    async def start_monitor(self) -> None:
        if not self.kiwoom:
            raise RuntimeError("Kiwoom missing")
        self.monitor = RealtimeMonitor(self.kiwoom, self.message_queue)
        await self.monitor.start()
        # 🔧 방금 생성한 인스턴스를 명시적으로 재확인 (mypy Optional 타입 좁히기용,
        # 실제로 None일 수 없으므로 순수 타입 안전망)
        assert self.monitor is not None

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

    async def _check_universe_source(self) -> None:
        """유니버스 폴백/스테일 여부를 점검하고 알림을 보낸다(조용한 폴백 금지)."""
        try:
            from infrastructure.market_data.universe_provider import (
                get_csv_age_days,
                get_last_source,
            )
            source = get_last_source()
            age = get_csv_age_days()
        except Exception as e:
            logger.warning(f"유니버스 소스 확인 실패: {e}")
            return

        self.startup_details["universe_source"] = source
        self.startup_details["universe_csv_age_days"] = (
            round(age, 2) if age is not None else None
        )
        if source == "fallback":
            logger.critical(
                "🚨 유니버스가 하드코딩 폴백으로 로드됨 — data/krx_universe.csv 확인 필요"
            )
            await self._send_error_alert(
                "유니버스 폴백 사용: data/krx_universe.csv 없음/읽기 실패 → "
                "하드코딩 종목으로 동작 중 (신규상장/시총변동 미반영)"
            )
        elif source == "csv_stale":
            logger.warning(
                f"⚠️ 유니버스 CSV가 스테일({age:.1f}일 경과) — 자동 갱신(토 09:00) 대기"
            )
            await self._send_error_alert(
                f"유니버스 CSV 스테일: {age:.1f}일 경과 → 신규상장/시총변동 반영 지연. "
                f"`python -m scheduler.universe_fetcher` 즉시 실행 권장"
            )
        elif source == "csv":
            logger.info(
                "✅ 유니버스 소스: CSV (data/krx_universe.csv"
                + (f", 나이 {age:.1f}일)" if age is not None else ")")
            )
        else:
            logger.warning(f"유니버스 소스 미확인: {source}")

    async def start_regime_manager(self) -> None:
        await regime_manager.start()
        debug_tower.log("SYSTEM", "REGIME_MANAGER_STARTED", {})
        logger.info("RegimeManager started")

    async def init_analyzer(self) -> None:
        if not self.db or not self.kiwoom:
            raise RuntimeError("DB or Kiwoom missing")

        learner = FeedbackLearner(kiwoom_connector=self.kiwoom, db_manager=self.db)
        self.analyzer = DeepAnalyzer(db_manager=self.db, feedback_learner=learner)
        await self.analyzer.load_weights()

        # ── P3-1: 관측성 체인 배선 (이상탐지 → 드리프트 → 근본원인 → 알림) ──
        # cb_provider: 서킷브레이커 상태를 RCA에 공급 (risk/market_risk_monitor)
        self.ops_monitor = OpsMonitor(
            on_alert=self._send_ops_alert,
            cb_provider=cb_input_provider,
        )
        self.paper_trader = PaperTrader(db=self.db)
        logger.info("PaperTrader 초기화 (모의매매 측정 — 돈 안 씀)")

        # 🔴 2026-10-10: FeedbackLearner 결과 → OpsMonitor 드리프트 감지 배선
        if learner is not None and self.ops_monitor is not None:
            learner.ops_monitor = self.ops_monitor

        self.signal_pipeline = SignalPipeline(
            db_manager=self.db,
            realtime_price_provider=self._get_realtime_price,
            ops_monitor=self.ops_monitor,
        )
        logger.info("DeepAnalyzer + SignalPipeline(V10) initialized")
        if self.ops_monitor is not None:
            logger.info("OpsMonitor 배선 완료 (이상탐지 → 근본원인 → 텔레그램)")
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

    async def init_hyperparameter_tuner(self) -> None:
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
        # 🔴 2026-10-10: 지난 튜닝 결과 복원 (이전에는 재기동 시 소실됐다)
        try:
            saved_params = load_tuning_state()
            if saved_params and self.signal_pipeline is not None:
                self.signal_pipeline.update_hyperparameters(saved_params)
                logger.info(f"저장된 튜닝값 복원 적용: {saved_params}")
                debug_tower.log("SYSTEM", "TUNING_STATE_RESTORED", {"params": saved_params})
        except Exception as e:
            logger.warning(f"튜닝값 복원 실패(무시): {e}")

    async def _run_hyperparameter_tuning(self) -> None:
        if self.tuning_executor is None:
            logger.debug("TuningExecutor 미초기화 — 튜닝 스킵")
            return
        await self.tuning_executor.run(days=30)

    async def init_sentiment_pipeline(self) -> None:
        if get_runtime_mode().external_io_disabled:
            logger.warning("🧪 [SAFE MODE] SentimentPipeline 생략 (뉴스 크롤링 비활성)")
            return

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

        self.news_crawler = news_crawler
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

    def _wire_nonprice_factors(self) -> None:
        """P12-3: 감성/공시 팩터를 신호 파이프라인에 연결(기록 전용, 행동 변경 없음)."""
        if self.signal_pipeline is None:
            return
        try:
            self.signal_pipeline.sentiment_pipeline = self.sentiment_pipeline
            self.signal_pipeline.dart_connector = getattr(self, "dart_connector", None)
            logger.info(
                "비가격 팩터 연결: sentiment=%s dart=%s",
                "on" if self.sentiment_pipeline is not None else "off",
                "on" if getattr(self, "dart_connector", None) is not None else "off",
            )
        except Exception as e:                       # 방어적
            logger.warning(f"비가격 팩터 연결 실패(무시): {e}")

    async def init_data_sources(self) -> None:
        if get_runtime_mode().external_io_disabled:
            logger.warning("🧪 [SAFE MODE] DART 커넥터 생략 (외부 API 비활성)")
            return

        dart_key = os.getenv("DART_API_KEY")
        if dart_key:
            dart = DartConnector(api_key=dart_key)
            await dart.connect()
            self.dart_connector = dart
            logger.info("DART connector initialized")
        else:
            logger.warning("DART_API_KEY missing → financial data excluded")

    async def start_performance_tracker(self) -> None:
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
            # 🔴 학습 고리 연결: 학습된 가중치를 전략 선택에 실제 반영
            weight_sink=(
                self.signal_pipeline.apply_learned_weights
                if self.signal_pipeline is not None else None
            ),
        )
        performance_tracker.attach_bandit_bridge(self.bandit_bridge)
        logger.info("BanditFeedbackBridge attached to PerformanceTracker")

        await performance_tracker.start()
        logger.info("PerformanceTracker v3.0 started (5min update loop + Bandit feedback)")

    async def start_ab_framework(self) -> None:
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

        # 🔧 self.db가 Optional이므로 가드 추가. 정상 흐름에서 init_container()가
        # 먼저 실행되어 self.db가 항상 non-None이므로 실질적 동작 차이 없음.
        if self.db:
            ExecutionCalibrator(self.db, TelegramSender())
            logger.info("ExecutionCalibrator initialized")

    async def start_telegram_commands(self) -> None:
        if not get_runtime_mode().telegram_enabled:
            logger.warning("🧪 [SAFE MODE] 텔레그램 명령어 폴링 생략 (TELEGRAM_ENABLED=0/DRY_RUN)")
            return

        self.telegram_cmd = TelegramCommandHandler(
            token=os.getenv("TELEGRAM_BOT_TOKEN") or "",
            chat_id=os.getenv("TELEGRAM_CHAT_ID") or "",
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
            started = await self.telegram_cmd.start()
            logging.getLogger("telegram.ext").setLevel(logging.INFO)
            logging.getLogger("telegram.request").setLevel(logging.INFO)
            if started:
                logger.info("Telegram commands activated (natural language + analysis)")
            else:
                logger.warning(
                    "⚠️ Telegram commands 비활성 — 중복 폴링 Conflict (다른 인스턴스 실행 중?)"
                )
                await self._send_error_alert(
                    "텔레그램 중복 폴링 감지(getUpdates Conflict): 다른 인스턴스가 실행 중인 것 같아 "
                    "명령어 수신을 시작하지 않았습니다. 중복 실행을 확인하세요."
                )
        except Exception as e:
            logger.warning(f"Telegram start failed: {e}")
            self.telegram_cmd = None

    async def init_scheduler(self) -> None:
        """APScheduler 작업 등록.

        🔧 Session 41 수정: self.db가 Optional이므로, db 없이 calibrator를
        생성하지 않고 calibration 작업 등록도 건너뛰도록 함(정상 흐름에서는
        self.db가 항상 non-None이라 실질적 동작 차이 없음. 다만 미세한 로직
        변경이므로 명시적으로 밝힘).
        """
        self.scheduler = SchedulerManager()
        sched = config.scheduler

        sender = TelegramSender()
        daily_reporter = DailyReportGenerator(db_manager=self.db, telegram_sender=sender)
        weekly_pdf_gen = WeeklyPDFGenerator(db_manager=self.db, kiwoom_connector=self.kiwoom)
        feedback_learner = FeedbackLearner(self.kiwoom, self.db)
        # 🔴 2026-10-10: 드리프트 감지 배선 (스케줄 잡 경로)
        feedback_learner.ops_monitor = self.ops_monitor
        calibrator: Optional[ExecutionCalibrator] = ExecutionCalibrator(self.db, sender) if self.db else None

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
        if calibrator is not None:
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
            send_momentum_report,
            CronTrigger(day_of_week="mon-fri", hour=8, minute=30, timezone="Asia/Seoul"),
            "momentum_report", max_retries=2, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            self._run_hyperparameter_tuning,
            CronTrigger(day_of_week="sun", hour=3, minute=0, timezone="Asia/Seoul"),
            "hyperparameter_tuning", max_retries=1, retry_delay=60,
        )
        self.scheduler.add_job_with_retry(
            scheduled_correlation_check,
            CronTrigger(day_of_week="mon-fri", hour=17, minute=0, timezone="Asia/Seoul"),
            "correlation_check", max_retries=1, retry_delay=60,
        )
        self.scheduler.add_job_with_retry(
            settle_and_feed_ab,
            CronTrigger(day_of_week="mon-fri", hour=17, minute=15, timezone="Asia/Seoul"),
            "calibration_settle", max_retries=1, retry_delay=60,
        )
        self.scheduler.add_job_with_retry(
            self._run_paper_settle,
            CronTrigger(day_of_week="mon-fri", hour=17, minute=5, timezone="Asia/Seoul"),
            "paper_settle", max_retries=1, retry_delay=60,
        )
        self.scheduler.add_job_with_retry(
            self._run_db_backup,
            CronTrigger(hour=int(os.getenv("DB_BACKUP_HOUR", "7")), minute=30, timezone="Asia/Seoul"),
            "db_backup", max_retries=1, retry_delay=60,
        )
        self.scheduler.add_job_with_retry(
            self._run_disk_check,
            CronTrigger(minute=7, timezone="Asia/Seoul"),
            "disk_check", max_retries=1, retry_delay=60,
        )
        self.scheduler.add_job_with_retry(
            self._run_db_maintenance,
            CronTrigger(day_of_week="sun", hour=3, minute=40, timezone="Asia/Seoul"),
            "db_maintenance", max_retries=1, retry_delay=60,
        )
        self.scheduler.add_job_with_retry(
            self._run_supply_demand,
            CronTrigger(day_of_week="mon-fri", hour=16, minute=35, timezone="Asia/Seoul"),
            "supply_demand", max_retries=1, retry_delay=60,
        )
        self.scheduler.add_job_with_retry(
            self._run_morning_briefing,
            CronTrigger(day_of_week="mon-fri", hour=8, minute=35, timezone="Asia/Seoul"),
            "morning_briefing", max_retries=1, retry_delay=60,
        )
        self.scheduler.add_job_with_retry(
            self._run_evening_briefing,
            CronTrigger(day_of_week="mon-fri", hour=17, minute=25, timezone="Asia/Seoul"),
            "evening_briefing", max_retries=1, retry_delay=60,
        )
        self.scheduler.add_job_with_retry(
            scheduled_dashboard,
            CronTrigger(day_of_week="mon-fri", hour=17, minute=30, timezone="Asia/Seoul"),
            "dashboard", max_retries=1, retry_delay=30,
        )
        self.scheduler.add_job_with_retry(
            scheduled_market_risk_check,
            CronTrigger(day_of_week="mon-fri", hour=16, minute=45, timezone="Asia/Seoul"),
            "market_risk_check", max_retries=2, retry_delay=5,
        )
        self.scheduler.add_job_with_retry(
            scheduled_data_readiness_check,
            CronTrigger(day_of_week="sun", hour=9, minute=0, timezone="Asia/Seoul"),
            "data_readiness", max_retries=1, retry_delay=30,
        )
        self.scheduler.add_job_with_retry(
            scheduled_universe_refresh,
            CronTrigger(day_of_week="sat", hour=9, minute=0, timezone="Asia/Seoul"),
            "universe_refresh", max_retries=2, retry_delay=60,
        )
        self.scheduler.start()
        self.startup_details["job_count"] = 23
        log_event("SCHEDULER_STARTED", {"jobs": 16})
        logger.info(
            "Scheduler started (16 jobs registered, incl. momentum_report, "
            "market_risk_check, data_readiness, universe_refresh, "
            "correlation_check, calibration_settle, dashboard)"
        )

    async def start_workers(self) -> None:
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

        # 알림 워커: 큐를 소비해 전송(실패 시 보관) — 워커와 독립적으로 동작
        self._load_alert_backlog()
        self._alert_task = asyncio.create_task(self._alert_worker())
        self.all_tasks.append(self._alert_task)
        logger.info("Alert worker started (queue-based delivery)")

        # 기동 백업: 마지막 백업이 24시간보다 오래됐으면 즉시 1회
        self._backup_if_stale()
        # ⏪ 2026-10-15 재부팅 사고: 늦게 기동하면 16:30~17:15 잡을 통째로 놓친다
        #   → 오늘 데이터가 비어 있으면 백그라운드에서 1회 자가 복구한다.
        self._catchup_task = asyncio.create_task(self._catchup_daily_jobs())

        # 기동 디스크 검사(여유 부족이면 즉시 알림)
        _disk_task = asyncio.create_task(self._run_disk_check())
        self.all_tasks.append(_disk_task)

    async def start_supervisor(self) -> None:
        supervisor = SystemSupervisor()
        task = asyncio.create_task(supervisor.run())
        self.all_tasks.append(task)
        logger.info("SystemSupervisor started (background monitoring)")
        debug_tower.log("SYSTEM", "SUPERVISOR_STARTED", {})

    async def start_health_server(self) -> None:
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

    async def _health_endpoint(self, request: Any) -> Any:
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

        status: Dict[str, Any] = {
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
        if not is_trading_day() or not self.monitor or not self.kiwoom or not self.db:
            return
        await collect_daily_ohlcv(self.kiwoom, self.db, self.monitor.tickers)

    async def _run_macro_update(self) -> None:
        await fetch_macro_data(force=True)

    async def _run_phase_transition_check(self, sender: TelegramSender) -> None:
        if not is_trading_day() or not self.db:
            return
        try:
            validator = PhaseTransitionValidator()
            start_date = "2026-08-20"
            end_date = datetime.now().strftime("%Y-%m-%d")
            decisions: List[Dict[str, Any]] = await self.db.get_decisions_by_date_range(start_date, end_date)

            if len(decisions) < 50:
                await sender.send_raw(
                    f"📊 Phase 전환 검증: 샘플 부족 ({len(decisions)}/50)"
                )
                return

            stats: Dict[str, Any] = await self.db.get_feedback_stats(days=30)
            result = validator.validate({
                "start_date": start_date,
                "end_date": end_date,
                "total_signals": len(decisions),
                "win_rate": float(stats.get("win_rate", 0.5)),
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

    async def _strategy_worker(
        self,
        wid: int,
        analyzer: DeepAnalyzer,
        db: DatabaseManager,
        sender: TelegramSender,
    ) -> None:
        logger.info(f"Strategy Worker-{wid} started")
        debug_tower.log("SYSTEM", f"WORKER_START_{wid}", {})
        processed_count = 0

        while not self._shutdown_requested:
            try:
                try:
                    stock_data: Dict[str, Any] = await asyncio.wait_for(
                        self.message_queue.get(), timeout=1.0
                    )
                except TimeoutError:
                    continue

                self._last_data_time = time.time()

                # 🔴 2026-10-14 장외소음 차단: 정규장(09:00~15:35) 외 틱은 분석하지 않는다.
                #   (시간외단일가 틱 → 장 마감 후에도 이벤트·기록이 계속 쌓이던 문제)
                if not self._in_signal_window():
                    _dropped = getattr(self, "_offhours_dropped", 0) + 1
                    self._offhours_dropped = _dropped
                    if _dropped == 1 or _dropped % 200 == 0:
                        logger.info(f"장외 틱 무시 누적 {_dropped}건 (정규장 09:00~15:35만 분석)")
                    self.message_queue.task_done()
                    continue

                price = stock_data.get("price")
                if price is None or float(price) <= 0:
                    self.message_queue.task_done()
                    continue

                ticker = str(stock_data.get("ticker", "UNKNOWN"))
                debug_tower.log(ticker, "WORKER_PROCESS", {"worker": wid})

                # 종목당 심층 분석 간격 제한(급변은 즉시) — 2026-10-08 자원 폭발 대응
                _now_ts = time.time()
                try:
                    _chg_abs = abs(float(stock_data.get("change_rate") or 0.0))
                except (TypeError, ValueError):
                    _chg_abs = 0.0
                if not self._should_analyze(ticker, _chg_abs, _now_ts):
                    self.message_queue.task_done()
                    continue
                self._analysis_last_at[ticker] = _now_ts

                token = bind_trace_id(str(stock_data.get("trace_id", new_trace_id())))
                try:
                    if self.sentiment_pipeline is not None:
                        try:
                            stock_data = await self.sentiment_pipeline.enrich(stock_data)
                        except Exception as e_sent:
                            logger.debug(f"Sentiment enrich skipped ({ticker}): {e_sent}")

                    analysis: Dict[str, Any] = await analyzer.analyze(stock_data)

                    if (
                        self.signal_pipeline
                        and analysis.get("action") not in ("ERROR", "EVENT_EXIT")
                    ):
                        try:
                            v10_signal = await self.signal_pipeline.process(stock_data)
                            if hasattr(v10_signal, "confidence"):
                                analysis["v10_sqi"] = round(float(v10_signal.confidence), 3)
                            # 🔴 2026-10-10: 비가격 태그/팩터를 결정·알림·모의매매로 전달
                            _tags = getattr(v10_signal, "tags", None)
                            if _tags:
                                analysis["tags"] = list(_tags)
                            _factors = getattr(v10_signal, "factors", None)
                            if _factors:
                                analysis["factors"] = dict(_factors)
                        except Exception as e_pipe:
                            logger.debug(f"SignalPipeline skipped ({ticker}): {e_pipe}")

                    if analysis.get("action") != "ERROR":
                        await db.save_decision(analysis)

                    action = str(analysis.get("action", ""))
                    if action in (
                        "SIGNAL_ENTRY",
                        "EVENT_SL_TRAIL",
                        "EVENT_ATR_SPIKE",
                        "EVENT_TP_HIT",
                        "EVENT_EXIT",
                        "EVENT_LIFECYCLE_ADVICE",
                    ):
                        # 🔴 2026-10-12: 장외(시간외단일가 ~18:00) 틱으로 만든 신호는
                        #   체결 불가한 가격이라 오해를 부른다 → 거래시간에만 알림.
                        #   (기록·모의매매는 계속 — 측정 데이터는 유지)
                        # 🔴 2026-10-10 알림 정밀도: 같은 종목·행동 반복 알림 억제
                        _suppressed = (not self._alert_hours_ok()) or (
                            not self._should_send_alert(ticker, action)
                        )
                        if _suppressed:
                            debug_tower.log(ticker, "ALERT_SUPPRESSED", {"action": action})
                            success = False
                        else:
                            # 🔴 2026-10-10: 전송을 백그라운드 큐로 위임(워커 비블로킹).
                            #   네트워크 장애에도 워커가 멈추지 않고, 실패분은 보관→재시도.
                            _msg = sender.format(analysis)
                            if _msg is not None:
                                success = self._enqueue_alert(_msg, analysis)
                            elif not sender.bot or not sender.chat_id:
                                success = False
                            else:
                                success = True
                        # 🔴 모의매매 측정: 매매 신호를 기록(실제 돈 안 씀)
                        if action in ("SIGNAL_ENTRY", "BUY", "SELL") and self.paper_trader is not None:
                            self.paper_trader.record_signal(
                                ticker=ticker,
                                action=action,
                                price=float(
                                    analysis.get("price")
                                    or analysis.get("price_at_decision")
                                    or 0.0
                                ),
                                confidence=float(analysis.get("confidence") or 0.0),
                                regime=str(analysis.get("regime") or ""),
                                tags=analysis.get("tags"),
                                factors=analysis.get("factors"),
                            )
                        if success:
                            processed_count += 1
                            tp_str = f" TP{analysis.get('tp_level')}" if action == "EVENT_TP_HIT" else ""
                            logger.info(f"Worker-{wid} [{action}] {ticker}{tp_str}")
                        elif not _suppressed:
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

    async def run_main_loop(self) -> None:
        logger.info("Main loop started (V10)")
        log_event("MAIN_LOOP_START", {})
        self.start_time = time.time()

        while (
            not self._shutdown_requested
            and self._shutdown_event is not None
            and not self._shutdown_event.is_set()
        ):
            try:
                if self.safety_guard:
                    macro = get_cached_macro()
                    safety_result: Dict[str, Any] = self.safety_guard.check({
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
                        if safety_result.get("should_alert"):
                            # 🔴 2026-10-12: 차단 중 10초마다 CRITICAL 도배(로그 폭주)
                            #    → 알림 쿨다운(30분)에 맞춰 기록도 함께 억제
                            logger.critical(f"SafetyGuard triggered: {triggered}")
                        else:
                            # 유지 중 로그도 조건이 바뀌거나 30분마다만(디버그 로그 폭주 방지)
                            _conds = sorted(
                                t.get("condition") for t in triggered if isinstance(t, dict)
                            )
                            _now_sg = time.time()
                            if _conds != getattr(self, "_sg_last_conds", None) or (
                                _now_sg - getattr(self, "_sg_last_log_ts", 0.0) >= 1800
                            ):
                                logger.debug(f"SafetyGuard 차단 유지 중: {_conds}")
                                self._sg_last_conds = _conds
                                self._sg_last_log_ts = _now_sg
                        if safety_result.get("should_alert"):
                            conds = [t.get("condition") for t in triggered if isinstance(t, dict)]
                            await self._send_error_alert(
                                "SafetyGuard 차단 활성화",
                                str(conds)[:200],
                            )
                        await asyncio.sleep(10)
                        continue

                if self.kiwoom and not self.kiwoom.is_connected():
                    await self._reconnect()
                    await asyncio.sleep(1)
                    continue

                now_dt = datetime.now()
                if self._in_data_flow_window(now_dt):
                    elapsed = time.time() - self._last_data_time
                    if elapsed > _DATA_FLOW_TIMEOUT:
                        log_event("DATA_FLOW_TIMEOUT", {"seconds": _DATA_FLOW_TIMEOUT})
                        logger.error(f"Data flow timeout ({_DATA_FLOW_TIMEOUT}s) → reconnecting")
                        debug_tower.log("SYSTEM", "DATA_FLOW_TIMEOUT", {})
                        if self.kiwoom:
                            await self.kiwoom.disconnect()
                            await self.kiwoom.connect()
                        if self.monitor:
                            await self.monitor.resubscribe_all()
                        self._last_data_time = time.time()

                if self.monitor:
                    signals: List[Dict[str, Any]] = await self.monitor.scan()
                    for sig_data in signals:
                        try:
                            self.message_queue.put_nowait(sig_data)
                            debug_tower.log(
                                str(sig_data.get("ticker", "SYS")), "SIGNAL_ENQUEUED",
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

    async def _reconnect(self) -> None:
        if not self.kiwoom or not self.monitor:
            return
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

    async def _send_error_alert(self, msg: str, detail: str = "") -> None:
        """오류 알림 발송.

        🔴 2026-10-12 사고: SQLite lock 반복 → 같은 오류가 매번 발송돼 폭주.
           → ① 동일 오류(제목+상세) 15분 쿨다운 ② 전체 10분 내 10건 상한.
        """
        import time as _t

        now = _t.monotonic()
        seen = getattr(self, "_error_alert_last", None)
        if seen is None:
            seen = {}
            self._error_alert_last = seen
        key = f"{msg}|{detail[:80]}"
        last = seen.get(key)
        if last is not None and now - last < 900:
            logger.info(f"오류 알림 억제(동일 오류 15분 쿨다운): {msg}")
            return
        stamps = getattr(self, "_error_alert_stamps", None)
        if stamps is None:
            stamps = []
            self._error_alert_stamps = stamps
        stamps[:] = [t for t in stamps if now - t < 600]
        if len(stamps) >= 10:
            logger.warning(f"오류 알림 억제(10분 10건 상한 초과): {msg}")
            return
        seen[key] = now
        stamps.append(now)
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
                f"💾 블랙박스: {bb.get('file_count', 0)}개  {bb.get('total_size_mb', 0)}MB\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>V10 DDD + SignalPipeline + MAB + HyperTuner + Sentiment + SafetyGuard + PortfolioVaR↔OrderExecutor</i>"
            )
        else:
            msg = (
                f"{status_emoji} <b>V10 시스템 시작 실패</b>\n"
                f"📋 {details.get('error', '알 수 없음')}"
            )

        try:
            if not self._enqueue_admin_message(msg):
                sender = self._error_sender or TelegramSender()
                await sender.send_raw(msg)
        except Exception:
            pass

    async def _send_shutdown_notification(self, reason: str = "정상 종료") -> None:
        try:
            msg = (
                f"🟡 <b>V10 시스템 종료</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 {reason}\n"
                f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━━━━"
            )
            if not self._enqueue_admin_message(msg):
                sender = self._error_sender or TelegramSender()
                await sender.send_raw(msg)
        except Exception:
            pass

    def _get_system_stats(self) -> Dict[str, Any]:
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

        stats: Dict[str, Any] = {
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

    async def bootstrap(self, shutdown_event: Optional[asyncio.Event] = None) -> None:
        self._shutdown_event = shutdown_event or asyncio.Event()
        startup_success = False

        try:
            self.load_env()
            mode = load_runtime_mode(force=True)
            self.startup_details["runtime_mode"] = mode.label
            if mode.is_safe:
                logger.warning(f"🧪 안전모드 활성: {mode.summary()}")
            self.validate_env()
            self.manage_pid()

            self._original_exception_handlers = setup_global_exception_handler()
            log_event("SYSTEM_START", {"pid": os.getpid(), "version": "V10"})
            debug_tower.log("SYSTEM", "MAIN_START", {"pid": os.getpid()})

            collector_status.register("system", freshness_seconds=None)

            logger.info("=" * 60)
            logger.info("V10 System Bootstrap Starting... (v2.5.1)")
            logger.info("=" * 60)

            await self.start_supervisor()
            await self.init_telegram()

            if mode.external_io_disabled:
                logger.warning("🧪 [SAFE MODE] 거시 데이터 수집 생략 (외부 연결 없음)")
            else:
                logger.info("Initial macro data collection...")
                await fetch_macro_data(force=True)
            macro = get_cached_macro()
            logger.info(f"  KOSPI: {macro.kospi_trend:.2f}%  USD/KRW: {macro.usdkrw:.0f}  VIX: {macro.vix:.1f}")

            await self.init_container()
            await self.connect_kiwoom()
            await self.start_monitor()
            await self._check_universe_source()
            await self.start_regime_manager()
            await self.init_analyzer()
            await self.init_hyperparameter_tuner()
            await self.init_sentiment_pipeline()
            await self.init_data_sources()
            self._wire_nonprice_factors()
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

    # ============================================================
    # 알림 큐 (2026-10-10): 워커 비블로킹 전송 + 실패 보관/재시도
    # ============================================================
    def _enqueue_alert(self, text: str, report: Dict[str, Any]) -> bool:
        """알림을 큐에 넣는다(즉시 반환). 큐가 가득 차면 1건 폐기 후 False."""
        try:
            self._alert_queue.put_nowait((text, report))
            debug_tower.log(
                str(report.get("ticker", "")), "TELEGRAM_QUEUED",
                {"action": report.get("action")},
            )
            return True
        except asyncio.QueueFull:
            logger.error(f"알림 큐 포화({self._alert_queue.maxsize}건) — 1건 폐기")
            return False

    def _load_alert_backlog(self) -> None:
        """이전 실행에서 미전송으로 보관된 알림을 복구한다."""
        self._alert_backlog = []
        try:
            if self._alert_backlog_path.exists():
                items: List[Dict[str, Any]] = []
                for line in self._alert_backlog_path.read_text(
                    encoding="utf-8"
                ).splitlines():
                    if line.strip():
                        items.append(json.loads(line))
                self._alert_backlog = items[-self._alert_backlog_max:]
                if self._alert_backlog:
                    logger.warning(
                        f"미전송 알림 {len(self._alert_backlog)}건 복구 → 재전송 대기"
                    )
        except Exception as e:
            logger.warning(f"미전송 알림 복구 실패(무시): {e}")
            self._alert_backlog = []

    def _save_alert_backlog(self) -> None:
        """보관 중인 미전송 알림을 파일로 저장(0건이면 파일 제거)."""
        try:
            if not self._alert_backlog:
                if self._alert_backlog_path.exists():
                    self._alert_backlog_path.unlink()
                return
            self._alert_backlog_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._alert_backlog_path, "w", encoding="utf-8") as f:
                for item in self._alert_backlog:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"미전송 알림 보관 실패(무시): {e}")

    async def _retry_alert_backlog(self, sender: TelegramSender) -> None:
        """새 전송이 성공한 직후에만 보관분을 소량 재시도(네트워크 복구 판단)."""
        now = time.time()
        if now - self._alert_backlog_last_try < 60.0:
            return
        self._alert_backlog_last_try = now
        head, tail = self._alert_backlog[:5], self._alert_backlog[5:]
        remain: List[Dict[str, Any]] = []
        sent = 0
        for item in head:
            ok = await sender.send_formatted(
                str(item.get("text", "")), item.get("report") or {}
            )
            if ok:
                sent += 1
            else:
                remain.append(item)
        self._alert_backlog = remain + list(tail)
        self._save_alert_backlog()
        if sent:
            logger.info(
                f"밀린 알림 {sent}건 재전송 완료(대기 {len(self._alert_backlog)}건)"
            )

    async def _alert_worker(self) -> None:
        """알림 큐를 소비해 전송한다. 실패분은 보관하고 나중에 재시도한다."""
        sender = self._error_sender or TelegramSender()
        logger.info("알림 워커 시작(백그라운드 전송)")
        while True:
            if self._shutdown_requested and self._alert_queue.empty():
                break
            try:
                text, report = await asyncio.wait_for(
                    self._alert_queue.get(), timeout=1.0
                )
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            try:
                ok = await sender.send_formatted(text, report)
                if not ok:
                    self._alert_backlog.append({
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "text": text,
                        "report": report,
                    })
                    self._alert_backlog = self._alert_backlog[-self._alert_backlog_max:]
                    self._save_alert_backlog()
                    logger.warning(
                        f"알림 전송 실패 → 보관({len(self._alert_backlog)}건 대기)"
                    )
                elif self._alert_backlog:
                    await self._retry_alert_backlog(sender)
            except Exception as e:
                logger.warning(f"알림 워커 오류(무시): {e}")
            finally:
                self._alert_queue.task_done()

    def _persist_queue_remainder(self) -> None:
        """셧다운 시 큐에 남은 알림을 보관 파일로 옮긴다(유실 방지)."""
        try:
            moved = 0
            while not self._alert_queue.empty():
                text, report = self._alert_queue.get_nowait()
                self._alert_backlog.append({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "text": text,
                    "report": report,
                })
                self._alert_queue.task_done()
                moved += 1
            self._alert_backlog = self._alert_backlog[-self._alert_backlog_max:]
            self._save_alert_backlog()
            if moved:
                logger.info(f"미전송 알림 {moved}건 보관(다음 실행 시 재전송)")
        except Exception as e:
            logger.warning(f"미전송 알림 보관 실패(무시): {e}")

    async def shutdown(self) -> None:
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

        # 🔴 2026-10-10: 알림 큐 드레인(최대 8초) — 종료 직전 미전송 알림 소진 시도
        if self._alert_task is not None and not self._alert_task.done():
            try:
                await asyncio.wait_for(self._alert_queue.join(), timeout=8.0)
                logger.info("알림 큐 드레인 완료")
            except Exception:
                logger.warning(
                    f"알림 큐 미소진 {self._alert_queue.qsize()}건 → 보관 후 종료"
                )
                self._persist_queue_remainder()
            self._alert_task.cancel()

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

        # aiohttp 클라이언트 세션 정리 (Unclosed client session 경고 방지)
        for attr in ("news_crawler", "dart_connector"):
            obj = getattr(self, attr, None)
            if obj is not None and hasattr(obj, "disconnect"):
                try:
                    await obj.disconnect()
                    logger.info(f"{attr} disconnected (session closed)")
                except Exception as e:
                    logger.warning(f"{attr}.disconnect() failed: {e}")

        try:
            await regime_manager.stop()
        except Exception as e:
            logger.warning(f"regime_manager.stop() failed: {e}")

        if self.scheduler:
            try:
                self.scheduler.shutdown()
            except Exception as e:
                logger.warning(f"scheduler.shutdown() failed: {e}")

        if self.container:
            try:
                await self.container.shutdown()
            except Exception as e:
                logger.warning(f"container.shutdown() failed: {e}")

        try:
            summary = collector_status.get_summary()
            logger.info(
                f"Collector summary: healthy={summary.get('healthy', 0)}/{summary.get('total', 0)}"
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
