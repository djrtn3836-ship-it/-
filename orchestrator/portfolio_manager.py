"""
orchestrator/portfolio_manager.py - v1.1 FINAL (종료 로직 강화)
- 현재 보유 중인 모든 포지션 추적 (DeepAnalyzer의 trailing_stops 연동)
- 5분마다 포트폴리오 VaR 자동 갱신
- stop() 메서드에 타임아웃 및 예외 처리 강화
"""

import asyncio
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging

from risk.portfolio_var import PortfolioVaR, PortfolioRiskMetrics
from data.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "risk_config.yaml"


class PortfolioManager:
    """포트폴리오 상태 및 VaR 관리자 (싱글톤)"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._load_config()
        self._positions: Dict[str, Dict] = {}
        self._weights: Dict[str, float] = {}
        self._total_value: float = 0.0

        self.var_calculator = PortfolioVaR(
            confidence=self.confidence,
            num_simulations=self.num_simulations,
            lookback_days=self.lookback_days
        )
        self._last_var: Optional[PortfolioRiskMetrics] = None
        self._last_update_time: Optional[datetime] = None

        self.db = DatabaseManager()

        self._update_task: Optional[asyncio.Task] = None
        self._running = False

    def _load_config(self):
        default = {
            'var': {
                'confidence': 0.95,
                'num_simulations': 10000,
                'lookback_days': 252,
                'update_interval_seconds': 300,
            },
            'thresholds': {
                'severe': 5.0,
                'high': 3.0,
                'medium': 1.5,
            }
        }
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                    var_cfg = config.get('var', default['var'])
                    th_cfg = config.get('thresholds', default['thresholds'])
                    self.confidence = var_cfg.get('confidence', 0.95)
                    self.num_simulations = var_cfg.get('num_simulations', 10000)
                    self.lookback_days = var_cfg.get('lookback_days', 252)
                    self.update_interval = var_cfg.get('update_interval_seconds', 300)
                    self.threshold_severe = th_cfg.get('severe', 5.0)
                    self.threshold_high = th_cfg.get('high', 3.0)
                    self.threshold_medium = th_cfg.get('medium', 1.5)
                    logger.info(f"✅ VaR 설정 로드: 신뢰도 {self.confidence}, 시뮬레이션 {self.num_simulations}회")
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
        """백그라운드 갱신 중지 (v1.1 - 강화)"""
        if not self._running:
            return
        self._running = False
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
            try:
                await asyncio.wait_for(self._update_task, timeout=2.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
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
                    prev = ohlcv[i-1].get('close', 0)
                    curr = ohlcv[i].get('close', 0)
                    if prev > 0 and curr > 0:
                        returns.append((curr - prev) / prev)
                if returns:
                    returns_dict[ticker] = returns
            except Exception as e:
                logger.debug(f"⚠️ {ticker} 수익률 데이터 조회 실패: {e}")

        total_value = sum(p.get('current_price', 0) * p.get('qty', 0) for p in self._positions.values())
        if total_value == 0:
            return

        weights = {}
        for ticker, pos in self._positions.items():
            value = pos.get('current_price', 0) * pos.get('qty', 0)
            weights[ticker] = value / total_value if total_value > 0 else 0

        try:
            var_result = self.var_calculator.calculate(
                tickers=tickers,
                returns_dict=returns_dict,
                weights=weights
            )
            self._last_var = var_result
            self._last_update_time = datetime.now()
            self._weights = weights
            self._total_value = total_value

            logger.info(
                f"📊 포트폴리오 VaR 갱신: "
                f"VaR95={var_result.var_95:.2%}, "
                f"CVaR={var_result.cvar_95:.2%}, "
                f"조정계수={var_result.risk_adj_factor:.2f}"
            )

            var_pct = var_result.var_95 * 100
            if var_pct >= self.threshold_severe:
                logger.critical(f"🚨 포트폴리오 VaR {var_pct:.1f}% (심각)! 즉시 점검 필요")
            elif var_pct >= self.threshold_high:
                logger.warning(f"⚠️ 포트폴리오 VaR {var_pct:.1f}% (높음) → 비중 축소 고려")
        except Exception as e:
            logger.error(f"❌ 포트폴리오 VaR 계산 실패: {e}")

    def update_position(self, ticker: str, price: float, qty: float, entry_price: float = None, action: str = 'BUY'):
        if action in ['BUY', 'SIGNAL_ENTRY']:
            self._positions[ticker] = {
                'entry_price': entry_price or price,
                'current_price': price,
                'qty': qty,
                'entry_time': datetime.now().isoformat()
            }
            logger.debug(f"📈 포지션 추가: {ticker} {qty}주 @ {price:,.0f}원")
        elif action in ['SELL', 'EXIT']:
            if ticker in self._positions:
                del self._positions[ticker]
                logger.debug(f"📉 포지션 제거: {ticker}")
        else:
            if ticker in self._positions:
                self._positions[ticker]['current_price'] = price

        asyncio.create_task(self.update_var())

    def get_portfolio_risk(self) -> Optional[PortfolioRiskMetrics]:
        return self._last_var

    def get_positions(self) -> Dict:
        return self._positions

    def get_weights(self) -> Dict[str, float]:
        return self._weights

    def get_global_risk_penalty(self) -> float:
        if self._last_var is None:
            return 1.0
        return self._last_var.risk_adj_factor

    def get_status(self) -> Dict:
        return {
            'position_count': len(self._positions),
            'total_value': self._total_value,
            'last_var': {
                'var_95': self._last_var.var_95 if self._last_var else None,
                'cvar_95': self._last_var.cvar_95 if self._last_var else None,
                'risk_adj': self._last_var.risk_adj_factor if self._last_var else 1.0,
                'status': self._last_var.status if self._last_var else 'N/A',
            } if self._last_var else None,
            'last_update': self._last_update_time.isoformat() if self._last_update_time else None,
        }