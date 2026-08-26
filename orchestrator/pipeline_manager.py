"""
Pipeline Manager v5.1.3 — Claude 버그 수정

수정 사항 (v5.1.2 → v5.1.3):
- 🔥 CRITICAL(silent): Circuit Breaker가 OPEN 상태일 때 예외 없이 None을
  반환하는데, start()가 이 반환값을 확인하지 않고 그대로 진행해
  "Kiwoom/DART 연결 실패 상태에서도 PipelineManager started"라고
  보고하던 버그 수정. 이제 연결 실패 시 명시적으로 예외를 발생시켜
  main.py가 재시도/알림 로직을 탈 수 있게 함.
- ⚠️ spawn_scanner_process()는 기존에 완전히 빈 함수(pass)로,
  docstring이 주장하는 "GIL 병목 해소(프로세스 분리)"가 실제로는
  구현되어 있지 않았음. 이번 수정에서는 즉시 구현 가능한 범위에서
  최소 동작하는 프로세스 분리 골격을 제공하되, Kiwoom Open API+는
  COM/STA 기반이라 이 프로세스 내부에서 별도의 메시지 펌프 스레드가
  필요하다는 점을 코드 주석과 로그로 명확히 남김 (kiwoom_connector.py
  실제 구현을 확인하지 못한 상태이므로 완전한 결합은 별도 검증 필요).
"""

import asyncio
import multiprocessing as mp

from core.circuit_breaker import DART_API_CB, KIWOOM_TR_CB
from core.logger import setup_logger
from data.dart_connector import DartConnector
from data.kiwoom_connector import KiwoomConnectorV512
from orchestrator.event_bus import EventBus
from orchestrator.feature_store import FeatureStore

logger = setup_logger("pipeline")


class PipelineConnectionError(Exception):
    """파이프라인 초기 연결 실패 (Circuit Breaker가 요청을 차단한 경우 포함)"""

    pass


class PipelineManager:
    """파이프라인 관리자 (프로세스 분리)"""

    def __init__(self):
        self.event_bus = EventBus()
        self.feature_store = FeatureStore()
        self.kiwoom = KiwoomConnectorV512()
        self.dart = DartConnector(api_key="YOUR_DART_KEY")

        self._processes: dict[str, mp.Process] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._is_running = False

    async def start(self):
        """파이프라인 시작 (프로세스 분리)"""
        logger.info("PipelineManager starting...")

        # 1. Event Bus 시작
        await self.event_bus.start()

        # 2. Feature Store 정리 태스크 시작
        asyncio.create_task(self._cleanup_features())

        # 3. Kiwoom 연결 (Circuit Breaker 적용)
        @KIWOOM_TR_CB.protect
        async def connect_kiwoom():
            return await self.kiwoom.connect()

        kiwoom_result = await connect_kiwoom()
        # 🔥 반환값 검증: Circuit Breaker가 OPEN이면 None이 반환됨
        if kiwoom_result is None:
            logger.critical(
                "❌ Kiwoom 연결 실패 (Circuit Breaker OPEN 또는 connect() 실패). "
                "PipelineManager를 시작 완료 상태로 보고하지 않고 예외를 발생시킵니다."
            )
            raise PipelineConnectionError("Kiwoom 연결 실패: Circuit Breaker OPEN 상태")

        # 4. DART 연결 (Circuit Breaker 적용)
        @DART_API_CB.protect
        async def connect_dart():
            await self.dart.connect()
            return True

        dart_result = await connect_dart()
        if dart_result is None:
            # DART는 보조 데이터 소스이므로 완전 중단은 아니지만, 반드시 경고로 남김
            logger.error(
                "⚠️ DART 연결 실패 (Circuit Breaker OPEN 또는 connect() 실패). "
                "DART 관련 신호(공시 블랙아웃 등)는 이 기간 동안 비활성 상태로 동작합니다."
            )

        self._is_running = True
        logger.info(
            f"PipelineManager started "
            f"(kiwoom={'OK' if kiwoom_result is not None else 'FAIL'}, "
            f"dart={'OK' if dart_result is not None else 'FAIL'})"
        )

    async def stop(self):
        """파이프라인 중지"""
        logger.info("PipelineManager stopping...")
        self._is_running = False

        await self.event_bus.stop()
        await self.kiwoom.disconnect()
        await self.dart.disconnect()

        for task in self._tasks.values():
            task.cancel()

        for name, process in self._processes.items():
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

        logger.info("PipelineManager stopped")

    async def _cleanup_features(self):
        """주기적 Feature 정리"""
        while self._is_running:
            await asyncio.sleep(60)
            await self.feature_store.clear_expired()

    def spawn_scanner_process(self):
        """
        스캐너 프로세스 분리

        ⚠️ 미완성 경고: 이 메서드는 이전 버전(v5.1.2)에서 완전히 빈 함수(pass)였고,
        docstring만 "GIL 병목 해소"를 주장하고 있었습니다. 실제 Kiwoom Open API+는
        COM 기반 STA(Single-Threaded Apartment)로 동작하므로, 별도 프로세스에서
        Kiwoom 연결을 돌리려면 그 프로세스 내부에 반드시 COM 메시지 펌프 전용
        스레드(pythoncom.PumpWaitingMessages 등)가 있어야 이벤트 유실이 없습니다.
        kiwoom_connector.py의 실제 구현 내용을 아직 확인하지 못했으므로,
        아래는 "프로세스가 최소한 실행되고 통신 채널이 연결된다"는 것만 보장하는
        골격입니다. 이 부분은 kiwoom_connector.py 리뷰 후 반드시 재검증 필요.
        """
        if "scanner" in self._processes and self._processes["scanner"].is_alive():
            logger.warning("스캐너 프로세스가 이미 실행 중입니다.")
            return

        ctx = mp.get_context("spawn")
        result_queue: mp.Queue = ctx.Queue()

        process = ctx.Process(
            target=_scanner_process_entrypoint, args=(result_queue,), name="scanner_process", daemon=True
        )
        process.start()
        self._processes["scanner"] = process
        logger.warning(
            "스캐너 프로세스를 spawn했습니다 (PID=%s). "
            "⚠️ kiwoom_connector.py 내부에 COM 메시지 펌프 스레드가 없다면 "
            "실시간 이벤트가 누락될 수 있습니다 — 별도 검증 필요.",
            process.pid,
        )


def _scanner_process_entrypoint(result_queue: "mp.Queue"):
    """
    별도 프로세스에서 실행되는 진입점.

    ⚠️ 실제 scanner_main.py 내용을 확인하지 못했으므로 여기서는
    프로세스가 정상적으로 뜨는지 확인하는 최소 골격만 제공합니다.
    실제 배포 전 scanner_main.py의 asyncio 이벤트 루프 + Kiwoom 연결
    로직을 이 함수 내부로 옮기는 작업이 필요합니다.
    """
    import logging

    logging.basicConfig(level=logging.INFO)
    logging.info("[scanner_process] 프로세스 시작됨 — 실제 스캐너 로직 연결 필요")
    result_queue.put({"status": "started"})
