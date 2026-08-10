"""
Deep Analyzer v5.1.2
후보군 심층 분석 (13개 지표 + 호가잔량)
- 수정: 리포트용 종목명, 가격, 근거 텍스트 추가 반환
"""

from typing import Dict, List, Optional

from core.logger import setup_logger
from filters.macro_filter import MacroFilter
from filters.sector_filter import SectorFilter
from filters.stock_filter import StockFilter
from filters.korean_special_filter import KoreanSpecialFilter
from filters.dynamic_weighter import DynamicWeighter
from decision.hybrid_decider import HybridDecider

logger = setup_logger("analyzer")


class DeepAnalyzer:
    """심층 분석기 (13개 지표 + 호가잔량 기반)"""

    def __init__(self):
        self.macro = MacroFilter()
        self.sector = SectorFilter()
        self.stock = StockFilter()
        self.korean = KoreanSpecialFilter()
        self.weighter = DynamicWeighter()
        self.decider = HybridDecider()

    async def analyze(self, stock: Dict) -> Dict:
        """
        심층 분석 실행

        Args:
            stock: 종목 데이터 (ticker, name, price, regime, flow, timestamp 등)

        Returns:
            분석 결과 (의사결정 + 리포트용 메타 정보 포함)
        """
        try:
            # 1. 각 필터 점수 계산 (13개 지표 + 호가잔량)
            macro_score = self.macro.check(stock)
            sector_score = self.sector.check(stock)
            stock_score = self.stock.check(stock)
            korean_score = self.korean.check(stock)

            # 2. 동적 가중치 (시장 국면/자금 흐름 기반)
            weights = self.weighter.calculate({
                "regime": stock.get("regime", "Sideways"),
                "flow": stock.get("flow", {})
            })

            # 3. 최종 점수 (가중 합산)
            final_score = (
                macro_score["score"] * weights.get("trend_weight", 0.3) +
                sector_score["score"] * weights.get("risk_weight", 0.2) +
                stock_score["score"] * weights.get("flow_weight", 0.4) +
                korean_score["score"] * 0.1  # 한국 특화 팩터 고정 가중치
            )

            # 4. 하이브리드 의사결정 (BUY/SELL/HOLD + 신뢰도 + 근거)
            decision = self.decider.decide({
                "score": final_score,
                "macro": macro_score,
                "sector": sector_score,
                "stock": stock_score,
                "korean": korean_score
            })

            # 5. 종목명 확보 (없으면 티커로 대체)
            ticker = stock.get("ticker", "")
            stock_name = stock.get("name", stock.get("stock_name", ticker))

            # 6. 리포트용 근거 텍스트 (decision에 있으면 사용, 없으면 기본값)
            positives = decision.get("reasons", decision.get("positives", ["다중 팩터 우위"]))
            negatives = decision.get("risks", decision.get("negatives", ["시장 변동성"]))
            counterfactuals = decision.get("counterfactuals", [])

            # 7. 최종 반환 (기존 필드 + 리포트용 추가 필드)
            return {
                # ---- 리포트/UI 표시용 필드 ----
                "name": stock_name,                    # 종목명
                "price": stock.get("price", 0.0),      # 현재가 (진입 참고가로 사용)
                "positives": positives,                # 매수 근거 (리포트용)
                "negatives": negatives,                # 매도/리스크 근거
                "counterfactuals": counterfactuals,    # 반사실적 분석

                # ---- 기존 코어 필드 ----
                "ticker": ticker,
                "action": decision["action"],          # "BUY" / "SELL" / "HOLD"
                "score": final_score,                  # 0.0 ~ 1.0 정규화 점수
                "confidence": decision.get("confidence", 0.5),

                # ---- 디버깅/심층 분석용 상세 점수 ----
                "details": {
                    "macro": macro_score["score"],
                    "sector": sector_score["score"],
                    "stock": stock_score["score"],
                    "korean": korean_score["score"]
                },
                "timestamp": stock.get("timestamp", "")
            }

        except Exception as e:
            logger.error(f"Analysis failed for {stock.get('ticker', 'unknown')}: {e}")
            return {
                "ticker": stock.get("ticker", ""),
                "name": stock.get("name", stock.get("stock_name", "")),
                "price": stock.get("price", 0.0),
                "action": "ERROR",
                "score": 0.0,
                "confidence": 0.0,
                "positives": [],
                "negatives": [],
                "counterfactuals": [],
                "details": {},
                "error": str(e),
                "timestamp": stock.get("timestamp", "")
            }