"""
scanner/deep_analyzer.py - v5.9.0 (트레일링 스탑 + 실시간 업데이트 알림)
- 기존 분석 로직 완전 유지
- 트레일링 스탑 상태 관리 및 업데이트 추가
- TRAILING_STOP_UPDATE, EXIT 액션 반환 가능
"""

import math
from typing import Dict, List, Optional, Any
import asyncio
from datetime import datetime

from core.logger import setup_logger
from data.db_manager import DatabaseManager
from filters.macro_filter import MacroFilter
from filters.sector_filter import SectorFilter
from filters.stock_filter import StockFilter
from filters.korean_special_filter import KoreanSpecialFilter
from filters.dynamic_weighter import DynamicWeighter
from decision.hybrid_decider import HybridDecider

logger = setup_logger("analyzer")


class DeepAnalyzer:
    def __init__(self, db_manager: DatabaseManager = None):
        self.db = db_manager
        self.macro = MacroFilter()
        self.sector = SectorFilter()
        self.stock = StockFilter()
        self.korean = KoreanSpecialFilter()
        self.weighter = DynamicWeighter()
        self.decider = HybridDecider()
        self.weights = {'momentum': 1.0, 'volume': 1.0, 'volatility': 1.0, 'macro': 1.0, 'sector': 1.0}
        
        # 🔥 트레일링 스탑 상태 저장
        self.trailing_stops: Dict[str, dict] = {}
        self._lock = asyncio.Lock()
        
        # 트레일링 설정 (config에서 가져올 수 있음)
        self.atr_multiplier_stop = 2.0      # 초기 손절 배수
        self.atr_multiplier_trail = 1.5     # 트레일링 반응 배수

    async def load_weights(self):
        if self.db:
            self.weights = await self.db.get_weights()
            logger.info(f"📊 최신 가중치 로드 완료: {self.weights}")
        else:
            logger.warning("⚠️ DB 매니저 없음 → 기본 가중치 사용")

    async def calculate_atr(self, ticker: str, period: int = 14) -> float:
        if not self.db:
            return 0.0
        try:
            ohlcv_list = await self.db.get_ohlcv(ticker, period)
            if len(ohlcv_list) < 2:
                return 0.0
            tr_values = []
            for i in range(1, len(ohlcv_list)):
                high = ohlcv_list[i]['high']
                low = ohlcv_list[i]['low']
                prev_close = ohlcv_list[i-1]['close']
                tr1 = high - low
                tr2 = abs(high - prev_close)
                tr3 = abs(low - prev_close)
                tr = max(tr1, tr2, tr3)
                tr_values.append(tr)
            if not tr_values:
                return 0.0
            atr = sum(tr_values) / len(tr_values)
            return round(atr, 2)
        except Exception as e:
            logger.error(f"❌ {ticker} ATR 계산 오류: {e}")
            return 0.0

    # ============================================================
    # 🔥 트레일링 스탑 코어 로직 (기존 분석에 추가)
    # ============================================================
    async def _update_trailing_stop(self, ticker: str, current_price: float, atr: float) -> Optional[Dict]:
        """트레일링 스탑 갱신. 변경 시 액션 딕셔너리 반환, 없으면 None"""
        async with self._lock:
            state = self.trailing_stops.get(ticker)
            if not state:
                return None

            action = state.get("action")  # "BUY" or "SELL"
            entry_price = state.get("entry_price")
            current_stop = state.get("current_stop")
            highest_price = state.get("highest_price", entry_price)
            lowest_price = state.get("lowest_price", entry_price)

            updated = False
            new_stop = current_stop

            if action == "BUY":
                if current_price > highest_price:
                    highest_price = current_price
                    state["highest_price"] = highest_price
                    new_stop = highest_price - (atr * self.atr_multiplier_trail)
                    if new_stop > current_stop:
                        new_stop = max(new_stop, current_stop)
                        state["current_stop"] = new_stop
                        updated = True
                        logger.info(f"📈 {ticker} 트레일링 스탑 상승: {current_stop:.0f} → {new_stop:.0f}")
            elif action == "SELL":
                if current_price < lowest_price:
                    lowest_price = current_price
                    state["lowest_price"] = lowest_price
                    new_stop = lowest_price + (atr * self.atr_multiplier_trail)
                    if new_stop < current_stop:
                        new_stop = min(new_stop, current_stop)
                        state["current_stop"] = new_stop
                        updated = True
                        logger.info(f"📉 {ticker} 트레일링 스탑 하락: {current_stop:.0f} → {new_stop:.0f}")

            if updated:
                return {
                    "ticker": ticker,
                    "action": "TRAILING_STOP_UPDATE",
                    "price": current_price,
                    "entry_price": entry_price,
                    "old_stop": current_stop,
                    "new_stop": new_stop,
                    "highest_price": state.get("highest_price"),
                    "lowest_price": state.get("lowest_price"),
                    "atr": atr,
                    "timestamp": datetime.now().isoformat(),
                }

            # 손절 도달 체크
            if action == "BUY" and current_price <= current_stop:
                logger.warning(f"🔴 {ticker} 트레일링 스탑 도달! (매수 청산)")
                return {
                    "ticker": ticker,
                    "action": "EXIT",
                    "reason": "TRAILING_STOP_HIT",
                    "price": current_price,
                    "entry_price": entry_price,
                    "stop_price": current_stop,
                    "highest_price": state.get("highest_price"),
                    "timestamp": datetime.now().isoformat(),
                }
            elif action == "SELL" and current_price >= current_stop:
                logger.warning(f"🔴 {ticker} 트레일링 스탑 도달! (매도 청산)")
                return {
                    "ticker": ticker,
                    "action": "EXIT",
                    "reason": "TRAILING_STOP_HIT",
                    "price": current_price,
                    "entry_price": entry_price,
                    "stop_price": current_stop,
                    "lowest_price": state.get("lowest_price"),
                    "timestamp": datetime.now().isoformat(),
                }
            return None

    # ============================================================
    # 메인 분석 함수 (기존 로직 유지 + 트레일링 스탑 추가)
    # ============================================================
    async def analyze(self, stock: Dict) -> Dict:
        try:
            ticker = stock.get('ticker', '')
            
            # ---- 1. 기존 분석 (완전 유지) ----
            macro_score = self.macro.check(stock)
            sector_score = self.sector.check(stock)
            stock_score = self.stock.check(stock)
            korean_score = self.korean.check(stock)

            atr = await self.calculate_atr(ticker, 14) if self.db else 0.0

            imbalance = stock.get('imbalance', 0.5)
            if not isinstance(imbalance, (int, float)) or imbalance < 0 or imbalance > 1:
                imbalance = 0.5

            action = stock.get('action', 'HOLD')
            if action == 'BUY':
                imbalance_factor = imbalance
            elif action == 'SELL':
                imbalance_factor = 1 - imbalance
            else:
                imbalance_factor = 0.5

            weights = self.weighter.calculate({
                "regime": stock.get("regime", "Sideways"),
                "flow": stock.get("flow", {})
            })

            base_score = (
                macro_score["score"] * weights.get("trend_weight", 0.3) +
                sector_score["score"] * weights.get("risk_weight", 0.2) +
                stock_score["score"] * weights.get("flow_weight", 0.4) +
                korean_score["score"] * 0.1
            )
            final_score = (base_score * 0.9) + (imbalance_factor * 0.1)
            final_score = max(0.0, min(1.0, final_score))

            decision = self.decider.decide({
                "score": final_score,
                "macro": macro_score,
                "sector": sector_score,
                "stock": stock_score,
                "korean": korean_score
            })

            positives = decision.get("reasons", decision.get("positives", ["다중 팩터 우위"]))
            pressure_text = stock.get('pressure', '')
            if pressure_text and pressure_text not in positives:
                positives.append(pressure_text)
            if atr > 0:
                positives.append(f"📊 ATR(14일): {atr:,.0f}원")
            else:
                positives.append("📊 ATR: 수집 중 (데이터 부족)")

            # 기본 분석 결과
            result = {
                "ticker": ticker,
                "name": stock.get("name", stock.get("ticker", "")),
                "price": stock.get("price", 0.0),
                "action": decision.get("action", "HOLD"),
                "score": final_score,
                "confidence": decision.get("confidence", 0.5),
                "positives": positives,
                "negatives": decision.get("risks", decision.get("negatives", ["시장 변동성 주의"])),
                "counterfactuals": decision.get("counterfactuals", []),
                "imbalance": imbalance,
                "atr": atr,
                "entry_price": stock.get("entry_price", stock.get("price", 0.0)),
                "details": {
                    "macro": macro_score["score"],
                    "sector": sector_score["score"],
                    "stock": stock_score["score"],
                    "korean": korean_score["score"],
                    "imbalance": imbalance,
                    "atr": atr,
                },
                "timestamp": stock.get("timestamp", "")
            }

            # ---- 2. 트레일링 스탑 처리 ----
            current_price = result["price"]
            if atr > 0 and current_price > 0:
                # 기존에 트레일링 상태가 있으면 업데이트
                trail_update = await self._update_trailing_stop(ticker, current_price, atr)
                if trail_update:
                    # 트레일링 업데이트 또는 청산이 발생하면 해당 액션 반환
                    return trail_update

                # 신규 진입 신호(BUY/SELL)이고 아직 트레일링 상태가 없으면 등록
                if result["action"] in ["BUY", "SELL"]:
                    async with self._lock:
                        if ticker not in self.trailing_stops:
                            entry_price = current_price
                            if result["action"] == "BUY":
                                stop_price = entry_price - (atr * self.atr_multiplier_stop)
                            else:
                                stop_price = entry_price + (atr * self.atr_multiplier_stop)
                            self.trailing_stops[ticker] = {
                                "action": result["action"],
                                "entry_price": entry_price,
                                "current_stop": stop_price,
                                "highest_price": entry_price if result["action"] == "BUY" else None,
                                "lowest_price": entry_price if result["action"] == "SELL" else None,
                                "atr": atr,
                                "entry_time": datetime.now().isoformat(),
                            }
                            logger.info(f"✅ {ticker} 트레일링 스탑 추적 시작 (진입가: {entry_price}, 초기 손절: {stop_price})")
                            # 결과에 트레일링 정보 추가
                            result["current_stop"] = stop_price
                            result["trailing_active"] = True

            return result

        except Exception as e:
            logger.error(f"Analysis failed for {stock.get('ticker', 'unknown')}: {e}")
            return {
                "ticker": stock.get("ticker", ""),
                "name": stock.get("name", stock.get("ticker", "")),
                "price": stock.get("price", 0.0),
                "action": "ERROR",
                "score": 0.0,
                "confidence": 0.0,
                "positives": [],
                "negatives": [],
                "counterfactuals": [],
                "atr": 0.0,
                "details": {},
                "error": str(e),
                "timestamp": stock.get("timestamp", "")
            }

    async def clear_trailing_stop(self, ticker: str):
        async with self._lock:
            if ticker in self.trailing_stops:
                del self.trailing_stops[ticker]
                logger.info(f"🗑️ {ticker} 트레일링 스탑 상태 제거")