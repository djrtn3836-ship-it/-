"""
tests/unit/test_stock_filter.py - StockFilter 단위 테스트
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from filters.stock_filter import StockFilter


class TestStockFilter:
    """StockFilter 단위 테스트"""

    def setup_method(self):
        self.filter = StockFilter()

    def test_check_normal(self, sample_tech_data):
        """정상 데이터 체크 테스트"""
        data = {
            "price": 83000.0,
            "ma_20": 81800.0,
            "rsi": 65.0,
            "volume_ratio": 1.8,
            "per": 15.0,
            "sector_avg_per": 18.0,
            "institution_net": 50.0,
            "adx": 32.0,
            "eps_growth": 12.0,
            "roe": 15.0,
            "fcf": 100.0,
            "orderbook_imbalance": 0.4,
            "trade_intensity": 1.3,
            "bid_ask_spread": 0.0005,
        }
        result = self.filter.check(data, regime="Bull", atr=1200.0)

        assert "score" in result
        assert "details" in result
        assert "passed" in result
        assert 0.0 <= result["score"] <= 1.0
        assert result["regime_used"] == "Bull"
        assert result["config_loaded"] is True or result["config_loaded"] is False  # Bool
        print(f"✅ 정상 체크 테스트 통과: 점수 {result['score']:.3f}, 통과 {result['passed']}")

    def test_check_bear_regime(self, sample_tech_data):
        """Bear 국면에서의 적응형 테스트"""
        data = {
            "price": 78000.0,
            "ma_20": 80000.0,
            "rsi": 35.0,  # Bear에서는 40 이하가 과매도
            "volume_ratio": 0.8,
            "per": 10.0,
            "sector_avg_per": 18.0,
            "institution_net": -20.0,
            "adx": 25.0,
            "eps_growth": 5.0,
            "roe": 8.0,
            "fcf": -10.0,
            "orderbook_imbalance": -0.3,
            "trade_intensity": 0.7,
            "bid_ask_spread": 0.001,
        }
        result = self.filter.check(data, regime="Bear", atr=1500.0)

        # Bear에서는 RSI 35가 매수 신호로 해석될 가능성
        assert "details" in result
        assert "rsi" in result["details"]
        # RSI 상세에 '침체' 또는 '과매도'가 포함되어야 함
        rsi_detail = result["details"]["rsi"]
        assert ("침체" in rsi_detail) or ("과매도" in rsi_detail)
        print(f"✅ Bear 국면 테스트 통과: RSI 상세 '{rsi_detail}'")

    def test_check_data_missing(self):
        """데이터 부족 시 크래시 방지 테스트"""
        data = {
            "ticker": "005930",
            "price": 0,  # 가격 없음
        }
        try:
            result = self.filter.check(data, regime="Sideways", atr=0.0)
            assert "score" in result
            assert result["score"] >= 0.0
            # 데이터 부족에도 크래시 없이 점수 반환
            print(f"✅ 데이터 부족 테스트 통과: 점수 {result['score']:.3f}")
        except Exception as e:
            pytest.fail(f"데이터 부족 시 크래시 발생: {e}")

    def test_to_float_conversion(self):
        """_to_float 안전 변환 테스트"""
        # None 처리
        assert self.filter._to_float(None, 100.0) == 100.0
        # 문자열 처리
        assert self.filter._to_float("150.5", 0.0) == 150.5
        # 숫자 처리
        assert self.filter._to_float(200, 0.0) == 200.0
        # 잘못된 문자열
        assert self.filter._to_float("abc", 50.0) == 50.0
        print("✅ _to_float 변환 테스트 통과")

    def test_regime_config_loading(self):
        """Regime 설정 파일 로드 테스트"""
        config = self.filter._load_regime_config()
        assert "Bull" in config
        assert "Sideways" in config
        assert "Bear" in config
        assert "rsi_buy_threshold" in config["Bull"]
        assert "rsi_sell_threshold" in config["Bear"]
        print(f"✅ Regime 설정 로드 테스트 통과 (키 {len(config)}개)")
