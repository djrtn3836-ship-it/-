"""
Stock Filter v5.1.2 — 13개 지표 기반 종목 분석 (호가잔량 3개 포함)

변경사항:
1. 기존 10개 지표 유지
2. 신규 3개 호가잔량 피처 추가
   - Orderbook Imbalance (매수/매도 잔량 비율)
   - Trade Intensity (체결 강도)
   - Bid-Ask Spread (호가 스프레드)
3. 가중치 조정 (기존 0.15 → 0.12, 신규 0.05씩)
"""

from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class StockFilter:
    """종목 분석 필터 (13개 지표)"""
    
    def __init__(self):
        # 13개 지표 가중치
        self.feature_weights = {
            'rsi': 0.12,
            'volume_ratio': 0.12,
            'ma_20': 0.12,
            'per': 0.12,
            'institution_net': 0.12,
            'atr': 0.05,
            'adx': 0.05,
            'eps_growth': 0.05,
            'roe': 0.05,
            'fcf': 0.05,
            'orderbook_imbalance': 0.05,
            'trade_intensity': 0.05,
            'bid_ask_spread': 0.05
        }
    
    def check(self, data: Dict) -> Dict:
        """
        종목 분석 실행
        
        Returns:
            {
                'score': float (0~1),
                'details': Dict[str, str],
                'passed': bool (score >= 0.6)
            }
        """
        score = 0.0
        details = {}
        
        # ===== 1. RSI (40~70 구간 안전) =====
        rsi = data.get("rsi", 50)
        if 40 < rsi < 70:
            score += self.feature_weights['rsi']
            details['rsi'] = f"양호 ({rsi:.0f})"
        elif rsi >= 70:
            details['rsi'] = f"과열 ({rsi:.0f})"
        else:
            details['rsi'] = f"침체 ({rsi:.0f})"
        
        # ===== 2. 거래량 (평균 대비) =====
        volume_ratio = data.get("volume_ratio", 1.0)
        if volume_ratio > 1.2:
            score += self.feature_weights['volume_ratio']
            details['volume'] = f"증가 ({volume_ratio:.1f}배)"
        else:
            details['volume'] = f"보통 ({volume_ratio:.1f}배)"
        
        # ===== 3. 20일선 상회 =====
        price = data.get("price", 0)
        ma_20 = data.get("ma_20", price)
        if price > ma_20:
            score += self.feature_weights['ma_20']
            details['ma'] = f"상회 (gap: {((price/ma_20)-1)*100:.1f}%)"
        else:
            details['ma'] = f"하회 (gap: {((ma_20/price)-1)*100:.1f}%)"
        
        # ===== 4. PER (업종 대비) =====
        per = data.get("per", 0)
        sector_avg_per = data.get("sector_avg_per", per)
        if per < sector_avg_per:
            score += self.feature_weights['per']
            details['per'] = f"저평가 (PER {per:.0f}, 업종 {sector_avg_per:.0f})"
        else:
            details['per'] = f"고평가 (PER {per:.0f}, 업종 {sector_avg_per:.0f})"
        
        # ===== 5. 기관 수급 =====
        institution_net = data.get("institution_net", 0)
        if institution_net > 0:
            score += self.feature_weights['institution_net']
            details['institution'] = f"순매수 ({institution_net:.0f}억)"
        else:
            details['institution'] = f"순매도 ({institution_net:.0f}억)"
        
        # ===== 6. ATR (변동성 정상) =====
        atr_ratio = data.get("atr_ratio", 0.02)
        if 0.01 < atr_ratio < 0.05:
            score += self.feature_weights['atr']
            details['atr'] = f"정상 ({atr_ratio:.2%})"
        else:
            details['atr'] = f"변동 ({atr_ratio:.2%})"
        
        # ===== 7. ADX (추세 강도) =====
        adx = data.get("adx", 20)
        if adx > 25:
            score += self.feature_weights['adx']
            details['adx'] = f"추세 강함 ({adx:.0f})"
        else:
            details['adx'] = f"추세 약함 ({adx:.0f})"
        
        # ===== 8. EPS 성장률 =====
        eps_growth = data.get("eps_growth", 0)
        if eps_growth > 10:
            score += self.feature_weights['eps_growth']
            details['eps'] = f"성장 ({eps_growth:.0f}%)"
        else:
            details['eps'] = f"정체 ({eps_growth:.0f}%)"
        
        # ===== 9. ROE =====
        roe = data.get("roe", 0)
        if roe > 10:
            score += self.feature_weights['roe']
            details['roe'] = f"양호 ({roe:.0f}%)"
        else:
            details['roe'] = f"저조 ({roe:.0f}%)"
        
        # ===== 10. FCF =====
        fcf = data.get("fcf", 0)
        if fcf > 0:
            score += self.feature_weights['fcf']
            details['fcf'] = "양호 (순현금)"
        else:
            details['fcf'] = "부족 (현금흐름 마이너스)"
        
        # ===== 11. Orderbook Imbalance (호가잔량 불균형) =====
        imbalance = data.get("orderbook_imbalance", 0)
        if imbalance > 0.3:
            score += self.feature_weights['orderbook_imbalance']
            details['orderbook_imbalance'] = f"매수 우위 ({imbalance:.2f})"
        elif imbalance < -0.3:
            details['orderbook_imbalance'] = f"매도 우위 ({imbalance:.2f})"
        else:
            details['orderbook_imbalance'] = "중립"
        
        # ===== 12. Trade Intensity (체결 강도) =====
        intensity = data.get("trade_intensity", 1.0)
        if intensity > 1.2:
            score += self.feature_weights['trade_intensity']
            details['trade_intensity'] = f"강한 매수 ({intensity:.2f})"
        elif intensity < 0.8:
            details['trade_intensity'] = f"강한 매도 ({intensity:.2f})"
        else:
            details['trade_intensity'] = "중립"
        
        # ===== 13. Bid-Ask Spread (호가 스프레드) =====
        spread = data.get("bid_ask_spread", 0)
        if spread < 0.001:
            score += self.feature_weights['bid_ask_spread']
            details['bid_ask_spread'] = f"좁음 ({spread:.3%})"
        elif spread > 0.005:
            details['bid_ask_spread'] = f"넓음 ({spread:.3%})"
        else:
            details['bid_ask_spread'] = "보통"
        
        # ===== 최종 점수 =====
        passed = score >= 0.6
        
        return {
            "score": min(1.0, score),
            "details": details,
            "passed": passed,
            "feature_count": len(self.feature_weights)
        }