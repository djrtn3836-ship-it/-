"""
scanner/deep_analyzer.py - v5.4.5 (load_weights 메서드 추가)
- ATR 자동 계산 + Imbalance 점수 반영
- load_weights: DB에서 가중치 로드 (scanner_main 호환)
"""

import math
from typing import Dict, List, Optional, Any

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
        # 🔥 기본 가중치 (DB 로드 전까지 사용)
        self.weights = {'momentum': 1.0, 'volume': 1.0, 'volatility': 1.0, 'macro': 1.0, 'sector': 1.0}

    # ============================================================
    # 🔥 [신규] load_weights 메서드 (scanner_main 호환)
    # ============================================================
    async def load_weights(self):
        """DB에서 최신 가중치를 로드하여 메모리 캐시 갱신"""
        if self.db:
            self.weights = await self.db.get_weights()
            logger.info(f"📊 최신 가중치 로드 완료: {self.weights}")
        else:
            logger.warning("⚠️ DB 매니저 없음 → 기본 가중치 사용")

    # ============================================================
    # ATR 계산 함수 (14일 기본값)
    # ============================================================
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

            if len(tr_values) == 0:
                return 0.0

            atr = sum(tr_values) / len(tr_values)
            return round(atr, 2)

        except Exception as e:
            logger.error(f"❌ {ticker} ATR 계산 오류: {e}")
            return 0.0

    # ============================================================
    # 메인 분석 함수 (ATR 포함)
    # ============================================================
    async def analyze(self, stock: Dict) -> Dict:
        try:
            ticker = stock.get('ticker', '')
            
            # 1. 기존 필터 점수
            macro_score = self.macro.check(stock)
            sector_score = self.sector.check(stock)
            stock_score = self.stock.check(stock)
            korean_score = self.korean.check(stock)

            # 2. ATR 계산 (DB에서 14일 OHLCV 조회)
            atr = await self.calculate_atr(ticker, 14) if self.db else 0.0

            # 3. Imbalance 처리
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

            # 4. 동적 가중치 (메모리 캐시 사용)
            weights = self.weighter.calculate({
                "regime": stock.get("regime", "Sideways"),
                "flow": stock.get("flow", {})
            })

            # 5. 베이스 점수
            base_score = (
                macro_score["score"] * weights.get("trend_weight", 0.3) +
                sector_score["score"] * weights.get("risk_weight", 0.2) +
                stock_score["score"] * weights.get("flow_weight", 0.4) +
                korean_score["score"] * 0.1
            )

            # 6. 최종 점수 (Imbalance 10% 반영)
            final_score = (base_score * 0.9) + (imbalance_factor * 0.1)
            final_score = max(0.0, min(1.0, final_score))

            # 7. 의사결정
            decision = self.decider.decide({
                "score": final_score,
                "macro": macro_score,
                "sector": sector_score,
                "stock": stock_score,
                "korean": korean_score
            })

            # 8. positives에 ATR 정보 추가
            positives = decision.get("reasons", decision.get("positives", ["다중 팩터 우위"]))
            pressure_text = stock.get('pressure', '')
            if pressure_text and pressure_text not in positives:
                positives.append(pressure_text)
            
            if atr > 0:
                positives.append(f"📊 ATR(14일): {atr:,.0f}원")
            else:
                positives.append("📊 ATR: 수집 중 (데이터 부족)")

            return {
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