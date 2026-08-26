"""
execution/order_executor.py - v1.0 (P3-1: Paper Trading Order Executor)
- 3단계 안전장치: 포지션 크기, 일일 손실 한도, 중복 주문 방지
- 모의 주문 결과를 DB에 기록하고 Telegram 알림 전송
- 실제 키움 API 호출 없이 시뮬레이션 (Paper Mode)
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


from data.db_manager import DatabaseManager
from data.kiwoom_connector import KiwoomConnectorV512
from report.telegram_sender import TelegramSender
from validation.execution_simulator import RealisticExecutionSimulator

logger = logging.getLogger(__name__)


class OrderMode(Enum):
    PAPER = "paper"   # 모의투자 (Phase 2)
    LIVE = "live"     # 실계좌 (v10.0 이후)


@dataclass
class OrderRequest:
    """주문 요청"""
    ticker: str
    action: str              # "BUY" | "SELL"
    quantity: int
    price: float
    order_type: str = "LIMIT"   # "LIMIT" | "MARKET"
    mode: OrderMode = OrderMode.PAPER


@dataclass
class OrderResult:
    """주문 결과"""
    success: bool
    order_id: str | None
    filled_price: float
    filled_qty: int
    commission: float
    tax: float
    total_cost: float
    error_msg: str | None = None
    timestamp: str = ""


class OrderExecutor:
    """주문 실행기 (Paper Trading 전용)"""

    SAFETY_CHECKS = [
        "position_size_check",
        "daily_loss_limit_check",
        "duplicate_order_check",
    ]

    def __init__(
        self,
        kiwoom_connector: KiwoomConnectorV512,
        db_manager: DatabaseManager,
        telegram_sender: TelegramSender | None = None,
        mode: OrderMode = OrderMode.PAPER,
    ):
        self.kiwoom = kiwoom_connector
        self.db = db_manager
        self.telegram = telegram_sender or TelegramSender()
        self.mode = mode
        self.exec_sim = RealisticExecutionSimulator(max_slippage_bps=100.0, num_slices=3)

        self._daily_pnl = 0.0
        self._max_daily_loss = -0.02  # -2%
        self._open_orders: dict[str, OrderRequest] = {}
        self._position_lock = asyncio.Lock()

        # 현재 포지션 정보 (DB에서 주기적 갱신)
        self._positions: dict[str, dict] = {}

    async def initialize(self):
        """초기화: DB에서 현재 포지션 로드"""
        self._positions = {p["ticker"]: p for p in await self.db.get_positions()}
        logger.info(f"✅ OrderExecutor 초기화 완료 (포지션 {len(self._positions)}개)")

    # ============================================================
    # 메인 실행 메서드
    # ============================================================
    async def execute(self, request: OrderRequest) -> OrderResult:
        """
        주문 실행 (3단계 안전장치 적용)
        """
        # 1. 모드 검증
        if self.mode == OrderMode.LIVE:
            raise ValueError("Live mode is not yet supported. Use PAPER mode.")

        # 2. 안전장치 검증
        for check in self.SAFETY_CHECKS:
            passed, reason = await getattr(self, f"_{check}")(request)
            if not passed:
                logger.warning(f"⛔ 주문 거부 ({check}): {reason}")
                return OrderResult(
                    success=False,
                    order_id=None,
                    filled_price=0.0,
                    filled_qty=0,
                    commission=0.0,
                    tax=0.0,
                    total_cost=0.0,
                    error_msg=reason,
                    timestamp=datetime.now().isoformat(),
                )

        # 3. 주문 실행 (Paper)
        async with self._position_lock:
            result = await self._execute_paper(request)
            if result.success:
                self._open_orders[request.ticker] = request
                # DB에 포지션 반영
                await self._update_db_position(request, result)
                # 텔레그램 알림 (선택)
                await self._send_notification(request, result)
            return result

    # ============================================================
    # 안전장치 체크 (Private)
    # ============================================================
    async def _position_size_check(self, request: OrderRequest) -> tuple[bool, str]:
        """최대 포지션 크기 검증 (1회 1000주 이하)"""
        if request.quantity > 1000:
            return False, f"주문 수량 초과: {request.quantity} > 1000"
        return True, ""

    async def _daily_loss_limit_check(self, request: OrderRequest) -> tuple[bool, str]:
        """일일 손실 한도 검증 (-2% 초과 시 차단)"""
        # 오늘 손익 계산 (DB에서 outcomes 조회)
        today = datetime.now().strftime("%Y-%m-%d")
        decisions = await self.db.get_decisions_by_date(today)
        _ = 0.0
        for d in decisions:
            # outcome 조회 (간단히)
            # 실제로는 DB에서 outcome 테이블 조회 필요
            pass
        # 임시: 가상의 손실률
        if self._daily_pnl < self._max_daily_loss:
            return False, f"일일 손실 한도 초과: {self._daily_pnl:.1%}"
        return True, ""

    async def _duplicate_order_check(self, request: OrderRequest) -> tuple[bool, str]:
        """중복 주문 방지 (동일 종목 중복 진입 차단)"""
        if request.ticker in self._open_orders:
            return False, f"중복 주문: {request.ticker} (이미 주문 접수)"
        return True, ""

    # ============================================================
    # Paper 주문 실행 (시뮬레이션)
    # ============================================================
    async def _execute_paper(self, request: OrderRequest) -> OrderResult:
        """모의 주문 실행 (현재가 + 슬리피지 적용)"""
        try:
            # 1. 현재가 조회 (Kiwoom TR)
            price_data = await self.kiwoom.request_tr(request.ticker, "현재가")
            current_price = float(price_data.get("close", request.price)) if price_data else request.price

            # 2. 평균 거래량 조회 (DB)
            avg_volume = 0
            if self.db:
                ohlcv = await self.db.get_ohlcv(request.ticker, period=20)
                if ohlcv:
                    volumes = [d.get("volume", 0) for d in ohlcv if d.get("volume", 0) > 0]
                    avg_volume = int(sum(volumes) / len(volumes)) if volumes else 0

            # 3. 체결 시뮬레이션 (RealisticExecutionSimulator)
            sim_result = self.exec_sim.execute(
                ticker=request.ticker,
                action=request.action,
                price=current_price,
                volume=request.quantity,
                order_size=request.quantity,
                market_cap=1e12,          # 임시 (실제로는 stock_universe에서 가져올 수 있음)
                avg_daily_volume=avg_volume,
                current_time=datetime.now(),
                orderbook=None,            # Fallback 사용
            )

            if not sim_result.filled or sim_result.fill_ratio < 0.5:
                return OrderResult(
                    success=False,
                    order_id=None,
                    filled_price=0.0,
                    filled_qty=0,
                    commission=0.0,
                    tax=0.0,
                    total_cost=0.0,
                    error_msg=f"체결률 {sim_result.fill_ratio:.0%} (미체결)",
                    timestamp=datetime.now().isoformat(),
                )

            filled_price = sim_result.execution_price
            filled_qty = int(sim_result.fill_ratio * request.quantity)

            # 수수료 및 세금 계산
            commission = filled_price * filled_qty * 0.00015  # 0.015%
            tax = filled_price * filled_qty * 0.0018 if request.action == "SELL" else 0.0  # 0.18%

            order_id = f"PAPER_{datetime.now().strftime('%Y%m%d%H%M%S')}_{request.ticker}"

            return OrderResult(
                success=True,
                order_id=order_id,
                filled_price=filled_price,
                filled_qty=filled_qty,
                commission=commission,
                tax=tax,
                total_cost=(filled_price * filled_qty) + commission + tax,
                error_msg=None,
                timestamp=datetime.now().isoformat(),
            )

        except Exception as e:
            logger.error(f"❌ Paper 주문 실행 오류: {e}")
            return OrderResult(
                success=False,
                order_id=None,
                filled_price=0.0,
                filled_qty=0,
                commission=0.0,
                tax=0.0,
                total_cost=0.0,
                error_msg=str(e),
                timestamp=datetime.now().isoformat(),
            )

    # ============================================================
    # DB 업데이트 및 알림
    # ============================================================
    async def _update_db_position(self, request: OrderRequest, result: OrderResult):
        """DB 포지션 갱신"""
        if request.action in ["BUY", "SELL"]:
            # 포지션 테이블에 반영 (기존 포지션 업데이트 또는 신규)
            current_pos = self._positions.get(request.ticker)
            if request.action == "BUY":
                if current_pos:
                    # 평균단가 계산 (기존 보유 + 신규 매수)
                    total_qty = current_pos["qty"] + result.filled_qty
                    avg_price = (current_pos["entry_price"] * current_pos["qty"] + result.filled_price * result.filled_qty) / total_qty
                    await self.db.save_position(request.ticker, avg_price, result.filled_price, total_qty)
                else:
                    await self.db.save_position(request.ticker, result.filled_price, result.filled_price, result.filled_qty)
            else:  # SELL
                if current_pos:
                    new_qty = current_pos["qty"] - result.filled_qty
                    if new_qty <= 0:
                        await self.db.delete_position(request.ticker)
                    else:
                        await self.db.save_position(request.ticker, current_pos["entry_price"], result.filled_price, new_qty)

    async def _send_notification(self, request: OrderRequest, result: OrderResult):
        """주문 결과 텔레그램 알림"""
        msg = (
            f"📊 <b>Paper 주문 체결</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 종목: <b>{request.ticker}</b>\n"
            f"• 액션: {request.action}\n"
            f"• 주문가: {result.filled_price:,.0f}원\n"
            f"• 체결량: {result.filled_qty}주\n"
            f"• 수수료: {result.commission:,.0f}원\n"
            f"• 세금: {result.tax:,.0f}원\n"
            f"• 총 비용: {result.total_cost:,.0f}원\n"
            f"• 주문 ID: {result.order_id}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>🕒 {result.timestamp}</i>"
        )
        await self.telegram.send_raw(msg)

    # ============================================================
    # 상태 조회
    # ============================================================
    def get_open_orders(self) -> dict:
        return self._open_orders.copy()

    def get_positions(self) -> dict:
        return self._positions.copy()