"""
filters/stock_filter.py - v6.0 FINAL (설정 파일 연동 + 예외 처리 강화)
- config/regime_weights.yaml에서 Regime별 RSI/이평선 임계값 동적 로드
- 모든 수치 연산에 _to_float() 적용 (None/문자열/0 방어)
- 데이터 부족 시에도 크래시 없이 기본 점수 반환
- 적응형 RSI/이평선 (Bull/Bear/Sideways 대응)
"""

from typing import Dict, List, Optional, Any
import yaml
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# 설정 파일 경로
REGIME_CONFIG_PATH = Path(__file__).parent.parent / "config" / "regime_weights.yaml"

# 기본값 (설정 파일 없을 시 Fallback)
DEFAULT_CONFIG = {
    "Bull": {"rsi_buy_threshold": 30, "rsi_sell_threshold": 80, "ma_trend_multiplier": 1.2, "volume_bull_threshold": 1.5},
    "Sideways": {"rsi_buy_threshold": 30, "rsi_sell_threshold": 70, "ma_trend_multiplier": 1.0, "volume_bull_threshold": 1.2},
    "Bear": {"rsi_buy_threshold": 40, "rsi_sell_threshold": 60, "ma_trend_multiplier": 0.8, "volume_bull_threshold": 1.0},
}


class StockFilter:
    """종목 분석 필터 (v6.0 - 설정 파일 연동)"""

    def __init__(self):
        # 13개 지표 가중치 (기존 유지)
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
        # 🔥 v6.0: Regime 설정 로드
        self._regime_config = self._load_regime_config()

    def _load_regime_config(self) -> Dict:
        """config/regime_weights.yaml 로드 (없으면 기본값)"""
        if REGIME_CONFIG_PATH.exists():
            try:
                with open(REGIME_CONFIG_PATH, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                    # 기본값과 병합 (누락된 키는 기본값으로 채움)
                    merged = {}
                    for regime in ["Bull", "Sideways", "Bear"]:
                        merged[regime] = {**DEFAULT_CONFIG.get(regime, {}), **config.get(regime, {})}
                    logger.info(f"✅ Regime 설정 로드 완료: {list(merged.keys())}")
                    return merged
            except Exception as e:
                logger.warning(f"⚠️ Regime 설정 로드 실패: {e}, 기본값 사용")
        logger.info("📋 Regime 설정 파일 없음 → 기본값 사용")
        return DEFAULT_CONFIG

    def _to_float(self, value: Any, default: float = 0.0) -> float:
        """안전한 float 변환 (None, 문자열, 에러 방어)"""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def check(self, data: Dict, regime: str = "Sideways", atr: float = 0.0) -> Dict:
        """
        종목 분석 실행 (적응형 RSI/이평선 적용)

        Args:
            data: 종목 데이터 (price, ma_20, rsi 등)
            regime: 현재 시장 국면 ("Bull", "Sideways", "Bear")
            atr: 현재 변동성 (ATR)
        Returns:
            {
                'score': float (0~1),
                'details': Dict[str, str],
                'passed': bool (score >= 0.6),
                'regime_used': str,
                'config_loaded': bool (설정 파일 사용 여부)
            }
        """
        # 🔥 v6.0: Regime 설정 가져오기
        regime_cfg = self._regime_config.get(regime, self._regime_config.get("Sideways", {}))
        rsi_buy_threshold = self._to_float(regime_cfg.get("rsi_buy_threshold", 30))
        rsi_sell_threshold = self._to_float(regime_cfg.get("rsi_sell_threshold", 70))
        ma_trend_multiplier = self._to_float(regime_cfg.get("ma_trend_multiplier", 1.0))
        volume_threshold = self._to_float(regime_cfg.get("volume_bull_threshold", 1.2))

        # 데이터 안전 변환
        price = self._to_float(data.get("price", 0.0))
        ma_20 = self._to_float(data.get("ma_20", price))
        rsi = self._to_float(data.get("rsi", 50))
        volume_ratio = self._to_float(data.get("volume_ratio", 1.0))
        per = self._to_float(data.get("per", 0.0))
        sector_avg_per = self._to_float(data.get("sector_avg_per", per))
        institution_net = self._to_float(data.get("institution_net", 0.0))
        adx = self._to_float(data.get("adx", 20))
        eps_growth = self._to_float(data.get("eps_growth", 0.0))
        roe = self._to_float(data.get("roe", 0.0))
        fcf = self._to_float(data.get("fcf", 0.0))
        imbalance = self._to_float(data.get("orderbook_imbalance", 0.0))
        intensity = self._to_float(data.get("trade_intensity", 1.0))
        spread = self._to_float(data.get("bid_ask_spread", 0.0))

        score = 0.0
        details = {}

        # ===== 1. RSI (적응형) =====
        if rsi_buy_threshold < rsi < rsi_sell_threshold:
            rsi_score = 1.0 - abs(rsi - 50) / 50
            score += rsi_score * self.feature_weights['rsi']
            details['rsi'] = f"양호 ({rsi:.0f}) [Regime: {regime}]"
        elif rsi >= rsi_sell_threshold:
            details['rsi'] = f"과열 ({rsi:.0f}) [임계: {rsi_sell_threshold:.0f}]"
        else:
            details['rsi'] = f"침체 ({rsi:.0f}) [임계: {rsi_buy_threshold:.0f}]"

        # ===== 2. 거래량 =====
        if volume_ratio > volume_threshold:
            score += self.feature_weights['volume_ratio']
            details['volume'] = f"증가 ({volume_ratio:.1f}배)"
        else:
            details['volume'] = f"보통 ({volume_ratio:.1f}배)"

        # ===== 3. 20일선 상회 (적응형) =====
        atr_ratio = atr / price if price > 0 else 0.0
        is_trending = atr_ratio > 0.02

        if price <= 0 or ma_20 <= 0:
            details['ma'] = "데이터 부족 (가격/20일선 미확인)"
        elif price > ma_20:
            if is_trending:
                # 추세장: 이평선 돌파 신호에 가중치 부여 (multiplier 적용)
                weight = self.feature_weights['ma_20'] * ma_trend_multiplier
                score += weight
                gap = ((price / ma_20) - 1) * 100
                details['ma'] = f"상회 (gap: {gap:.1f}%) [추세장, 가중치 {ma_trend_multiplier:.1f}]"
            else:
                gap = ((price / ma_20) - 1) * 100
                details['ma'] = f"상회 (gap: {gap:.1f}%) [횡보장, 신호 무시]"
        else:
            gap = ((ma_20 / price) - 1) * 100
            details['ma'] = f"하회 (gap: {gap:.1f}%)"

        # ===== 4. PER =====
        if per > 0 and per < sector_avg_per:
            score += self.feature_weights['per']
            details['per'] = f"저평가 (PER {per:.0f}, 업종 {sector_avg_per:.0f})"
        elif per > 0:
            details['per'] = f"고평가 (PER {per:.0f}, 업종 {sector_avg_per:.0f})"
        else:
            details['per'] = "PER 데이터 없음"

        # ===== 5. 기관 수급 =====
        if institution_net > 0:
            score += self.feature_weights['institution_net']
            details['institution'] = f"순매수 ({institution_net:.0f}억)"
        else:
            details['institution'] = f"순매도 ({institution_net:.0f}억)"

        # ===== 6. ATR (변동성) =====
        if atr > 0 and price > 0:
            atr_ratio_calc = atr / price
            if 0.01 < atr_ratio_calc < 0.05:
                score += self.feature_weights['atr']
                details['atr'] = f"정상 ({atr_ratio_calc:.2%})"
            elif atr_ratio_calc > 0:
                details['atr'] = f"변동 ({atr_ratio_calc:.2%})"
            else:
                details['atr'] = "ATR 데이터 없음"

        # ===== 7. ADX =====
        if adx > 25:
            score += self.feature_weights['adx']
            details['adx'] = f"추세 강함 ({adx:.0f})"
        else:
            details['adx'] = f"추세 약함 ({adx:.0f})"

        # ===== 8. EPS 성장률 =====
        if eps_growth > 10:
            score += self.feature_weights['eps_growth']
            details['eps'] = f"성장 ({eps_growth:.0f}%)"
        else:
            details['eps'] = f"정체 ({eps_growth:.0f}%)"

        # ===== 9. ROE =====
        if roe > 10:
            score += self.feature_weights['roe']
            details['roe'] = f"양호 ({roe:.0f}%)"
        else:
            details['roe'] = f"저조 ({roe:.0f}%)"

        # ===== 10. FCF =====
        if fcf > 0:
            score += self.feature_weights['fcf']
            details['fcf'] = "양호 (순현금)"
        else:
            details['fcf'] = "부족 (현금흐름 마이너스)"

        # ===== 11. Orderbook Imbalance =====
        if imbalance > 0.3:
            score += self.feature_weights['orderbook_imbalance']
            details['orderbook_imbalance'] = f"매수 우위 ({imbalance:.2f})"
        elif imbalance < -0.3:
            details['orderbook_imbalance'] = f"매도 우위 ({imbalance:.2f})"
        else:
            details['orderbook_imbalance'] = "중립"

        # ===== 12. Trade Intensity =====
        if intensity > 1.2:
            score += self.feature_weights['trade_intensity']
            details['trade_intensity'] = f"강한 매수 ({intensity:.2f})"
        elif intensity < 0.8:
            details['trade_intensity'] = f"강한 매도 ({intensity:.2f})"
        else:
            details['trade_intensity'] = "중립"

        # ===== 13. Bid-Ask Spread =====
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
            "score": min(1.0, max(0.0, score)),
            "details": details,
            "passed": passed,
            "feature_count": len(self.feature_weights),
            "regime_used": regime,
            "config_loaded": REGIME_CONFIG_PATH.exists(),  # v6.0: 설정 파일 사용 여부
        }