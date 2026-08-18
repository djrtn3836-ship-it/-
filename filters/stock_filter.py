"""
filters/stock_filter.py - v5.1.4 (문자열 price/ma_20 방어 추가)
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
        
        # 🔥 문자열 price를 float으로 안전하게 변환
        raw_price = data.get("price", 0)
        try:
            price = float(raw_price) if raw_price is not None else 0.0
        except (ValueError, TypeError):
            price = 0.0
        
        # 🔥 문자열 ma_20도 float으로 안전하게 변환
        raw_ma_20 = data.get("ma_20", price)
        try:
            ma_20 = float(raw_ma_20) if raw_ma_20 is not None else 0.0
        except (ValueError, TypeError):
            ma_20 = 0.0
        
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
        
        # ===== 3. 20일선 상회 (ZeroDivision 방어) =====
        if price <= 0 or ma_20 <= 0:
            details['ma'] = "데이터 부족 (가격/20일선 미확인)"
        elif price > ma_20:
            score += self.feature_weights['ma_20']
            gap = ((price / ma_20) - 1) * 100
            details['ma'] = f"상회 (gap: {gap:.1f}%)"
        else:
            gap = ((ma_20 / price) - 1) * 100
            details['ma'] = f"하회 (gap: {gap:.1f}%)"
        
        # ===== 4. PER (업종 대비) =====
        per = data.get("per", 0)
        sector_avg_per = data.get("sector_avg_per", per)
        if per > 0 and per < sector_avg_per:
            score += self.feature_weights['per']
            details['per'] = f"저평가 (PER {per:.0f}, 업종 {sector_avg_per:.0f})"
        elif per > 0:
            details['per'] = f"고평가 (PER {per:.0f}, 업종 {sector_avg_per:.0f})"
        else:
            details['per'] = "PER 데이터 없음"
        
        # ===== 5. 기관 수급 =====
        institution_net = data.get("institution_net", 0)
        if institution_net > 0:
            score += self.feature_weights['institution_net']
            details['institution'] = f"순매수 ({institution_net:.0f}억)"
        else:
            details['institution'] = f"순매도 ({institution_net:.0f}억)"
        
        # ===== 6. ATR (변동성) — ZeroDivision 방어 =====
        atr_ratio = data.get("atr_ratio", 0.0)
        if atr_ratio == 0.0:
            atr = data.get("atr", 0.0)
            if price > 0 and atr > 0:
                atr_ratio = atr / price
            else:
                atr_ratio = 0.0
        
        if 0.01 < atr_ratio < 0.05:
            score += self.feature_weights['atr']
            details['atr'] = f"정상 ({atr_ratio:.2%})"
        elif atr_ratio > 0:
            details['atr'] = f"변동 ({atr_ratio:.2%})"
        else:
            details['atr'] = "ATR 데이터 없음"
        
        # ===== 7. ADX =====
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
        
        # ===== 11. Orderbook Imbalance =====
        imbalance = data.get("orderbook_imbalance", 0)
        if imbalance > 0.3:
            score += self.feature_weights['orderbook_imbalance']
            details['orderbook_imbalance'] = f"매수 우위 ({imbalance:.2f})"
        elif imbalance < -0.3:
            details['orderbook_imbalance'] = f"매도 우위 ({imbalance:.2f})"
        else:
            details['orderbook_imbalance'] = "중립"
        
        # ===== 12. Trade Intensity =====
        intensity = data.get("trade_intensity", 1.0)
        if intensity > 1.2:
            score += self.feature_weights['trade_intensity']
            details['trade_intensity'] = f"강한 매수 ({intensity:.2f})"
        elif intensity < 0.8:
            details['trade_intensity'] = f"강한 매도 ({intensity:.2f})"
        else:
            details['trade_intensity'] = "중립"
        
        # ===== 13. Bid-Ask Spread =====
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