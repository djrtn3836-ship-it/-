"""
orchestrator/portfolio_manager.py - v1.3 (OrderExecutor 콜백 연결)

v1.2 → v1.3 변경 사항:
    - PortfolioManager가 싱글톤임이 scanner/deep_analyzer.py, execution/order_executor.py
      소스 대조로 확정됨.
    - update_var() 완료 후 OrderExecutor.update_position_limit()을 자동으로 호출하는
      콜백 패턴 추가. ROADMAP.md에는 이 연동이 "✅ 완료"로 기록되어 있었으나,
      실제 코드에는 연결 고리가 전혀 없었음을 소스 대조로 확인하고 이번에 완성함.
    - set_order_executor_callback(): bootstrap.py에서 OrderExecutor를 주입받아
      순환 임포트 없이 연결.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

import yaml

from data.db_manager import DatabaseManager
from risk.portfolio_var import PortfolioRiskMetrics, PortfolioVaR

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "risk_config.yaml"


class PortfolioManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._load_config()
        self._positions: dict[str, dict] = {}
        self._weights: dict[str, float] = {}
        self._total_value: float = 0.0
        self._position_lock = asyncio.Lock()

        self.var_calculator = PortfolioVaR(
            confidence=self.confidence,
            num_simulations=self.num_simulations,
            lookback_days=self.lookback_days,
        )
        self._last_var: Optional[PortfolioRiskMetrics] = None
        self._last_update_time: Optional[datetime] = None

        # 🔧 참고(Phase 4 검토 대상): 여기서 별도의 DatabaseManager()를 생성하므로
        # container.db_manager / bootstrap.self.db와는 다른 Python 객체입니다.
        # 기본 경로가 동일한 물리 SQLite 파일(WAL 모드)을 가리켜 지금은 문제가
        # 없지만, 테스트 DB로 전환할 때는 이 인스턴스가 별도로 관리된다는 점에
        # 주의해야 합니다. 즉시 수정하지 않고 Phase 4(DI 통합) 논의 때 재검토합니다.
        self.db = DatabaseManager()

        self._update_task: Optional[asyncio.Task] = None
        self._running = False

        # 🆕 v1.3: OrderExecutor 콜백 (순환 임포트 방지용 지연 주입)
        self._position_limit_callback: Optional[Callable[[float], None]] = None

    def set_order_executor_callback(self, callback: Callable[[float], None]) -> None:
        """OrderExecutor.update_position_limit을 콜백으로 등록.

        bootstrap.py의 init_execution() 이후에 호출됩니다.
        순환 임포트 없이 PortfolioVaR → OrderExecutor 연결을 완성합니다.

        Args:
            callback: position_limit(float)을 인자로 받는 동기 callable
                      (실제로는 order_executor.update_position_limit)
        """
        self._position_limit_callback = callback
        logger.info("✅ PortfolioManager: OrderExecutor position_limit 콜백 등록 완료")

    def _load_config(self):
        default = {
            "var": {
                "confidence": 0.95,
                "num_simulations": 10000,
                "lookback_days": 252,
                "update_interval_seconds": 300,
            },
            "thresholds": {"severe": 5.0, "high": 3.0, "medium": 1.5},
        }
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    var_cfg = config.get("var", default["var"])
                    th_cfg = config.get("thresholds", default["thresholds"])
                    self.confidence = var_cfg.get("confidence", 0.95)
                    self.num_simulations = var_cfg.get("num_simulations", 10000)
                    self.lookback_days = var_cfg.get("lookback_days", 252)
                    self.update_interval = var_cfg.get("update_interval_seconds", 300)
                    self.threshold_severe = th_cfg.get("severe", 5.0)
                    self.threshold_high = th_cfg.get("high", 3.0)
                    self.threshold_medium = th_cfg.get("medium", 1.5)
                    logger.info(
                        f"✅ VaR 설정 로드: 신뢰도 {self.confidence}, "
                        f"시뮬레이션 {self.num_simulations}회"
                    )
                    return
            except Exception as e:
                logger.warning(f"⚠️ risk_config.yaml 로드 실패: {e}, 기본값 사용")
        self.confidence = 0.95
        self.num_simulations = 10000
        self.lookback_days = 252
        self.update_interval = 300
        self.threshold_severe = 5.0
        self.threshold_high = 3.0
        self.threshold_medium = 1.5

    async def start(self):
        if self._running:
            return
        self._running = True
        self._update_task = asyncio.create_task(self._update_loop())
        logger.info("✅ PortfolioManager 시작됨 (VaR 갱신 간격: %d초)", self.update_interval)

    async def stop(self):
        if not self._running:
            return
        self._running = False
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
            try:
                await asyncio.wait_for(self._update_task, timeout=2.0)
            except asyncio.CancelledError:
                pass
            except TimeoutError:
                logger.warning("⚠️ PortfolioManager 중단 타임아웃 (2초)")
            except Exception as e:
                logger.error(f"❌ PortfolioManager 중단 오류: {e}")
        logger.info("🛑 PortfolioManager 중지됨")

    async def _update_loop(self):
        await self.update_var()
        while self._running:
            await asyncio.sleep(self.update_interval)
            await self.update_var()

    async def update_var(self):
        if not self._positions:
            logger.debug("📭 포트폴리오 비어 있음 → VaR 계산 스킵")
            return

        returns_dict = {}
        tickers = list(self._positions.keys())
        for ticker in tickers:
            try:
                ohlcv = await self.db.get_ohlcv(ticker, period=self.lookback_days)
                if len(ohlcv) < 5:
                    continue
                returns = []
                for i in range(1, len(ohlcv)):
                    prev = ohlcv[i - 1].get("close", 0)
                    curr = ohlcv[i].get("close", 0)
                    if prev > 0 and curr > 0:
                        returns.append((curr - prev) / prev)
                if returns:
                    returns_dict[ticker] = returns
            except Exception as e:
                logger.debug(f"⚠️ {ticker} 수익률 데이터 조회 실패: {e}")

        total_value = sum(
            p.get("current_price", 0) * p.get("qty", 0) for p in self._positions.values()
        )
        if total_value == 0:
            return

        weights = {}
        for ticker, pos in self._positions.items():
            value = pos.get("current_price", 0) * pos.get("qty", 0)
            weights[ticker] = value / total_value if total_value > 0 else 0

        try:
            var_result = self.var_calculator.calculate(
                tickers=tickers, returns_dict=returns_dict, weights=weights
            )
            self._last_var = var_result
            self._last_update_time = datetime.now()
            self._weights = weights
            self._total_value = total_value

            logger.info(
                "📊 포트폴리오 VaR 갱신: VaR95=%.2f%%, CVaR=%.2f%%, 조정계수=%.2f, "
                "Kelly한도=%.2f, 최종한도=%.2f",
                var_result.var_95 * 100,
                var_result.cvar_95 * 100,
                var_result.risk_adj_factor,
                var_result.kelly_position_limit,
                var_result.position_limit,
            )

            # 🆕 v1.3: OrderExecutor에 position_limit 전달 (콜백 패턴)
            if self._position_limit_callback is not None:
                try:
                    self._position_limit_callback(var_result.position_limit)
                    logger.debug(
                        "📊 position_limit → OrderExecutor 전달: %.2f",
                        var_result.position_limit,
                    )
                except Exception as cb_err:
                    logger.warning(
                        "⚠️ OrderExecutor position_limit 콜백 실패 (비치명): %s", cb_err
                    )

            var_pct = var_result.var_95 * 100
            if var_pct >= self.threshold_severe:
                logger.critical("🚨 포트폴리오 VaR %.1f%% (심각)! 즉시 점검 필요", var_pct)
            elif var_pct >= self.threshold_high:
                logger.warning("⚠️ 포트폴리오 VaR %.1f%% (높음) → 비중 축소 고려", var_pct)
        except Exception as e:
            logger.error(f"❌ 포트폴리오 VaR 계산 실패: {e}")

    async def update_position(
        self, ticker: str, price: float, qty: float,
        entry_price: float = None, action: str = "BUY",
    ):
        async with self._position_lock:
            if action in ["BUY", "SIGNAL_ENTRY"]:
                self._positions[ticker] = {
                    "entry_price": entry_price or price,
                    "current_price": price,
                    "qty": qty,
                    "entry_time": datetime.now().isoformat(),
                }
                logger.debug(f"📈 포지션 추가: {ticker} {qty}주 @ {price:,.0f}원")
            elif action in ["SELL", "EXIT"]:
                if ticker in self._positions:
                    del self._positions[ticker]
                    logger.debug(f"📉 포지션 제거: {ticker}")
            else:
                if ticker in self._positions:
                    self._positions[ticker]["current_price"] = price

        asyncio.create_task(self.update_var())

    def get_portfolio_risk(self) -> Optional[PortfolioRiskMetrics]:
        return self._last_var

    def get_positions(self) -> dict:
        return self._positions

    def get_weights(self) -> dict[str, float]:
        return self._weights

    def get_global_risk_penalty(self) -> float:
        if self._last_var is None:
            return 1.0
        return self._last_var.risk_adj_factor

    def get_status(self) -> dict:
        return {
            "position_count": len(self._positions),
            "total_value": self._total_value,
            "last_var": {
                "var_95": self._last_var.var_95 if self._last_var else None,
                "cvar_95": self._last_var.cvar_95 if self._last_var else None,
                "risk_adj": self._last_var.risk_adj_factor if self._last_var else 1.0,
                "kelly_limit": self._last_var.kelly_position_limit if self._last_var else 1.0,
                "position_limit": self._last_var.position_limit if self._last_var else 1.0,
                "status": self._last_var.status if self._last_var else "N/A",
            } if self._last_var else None,
            "last_update": self._last_update_time.isoformat() if self._last_update_time else None,
            "order_executor_connected": self._position_limit_callback is not None,
        }
