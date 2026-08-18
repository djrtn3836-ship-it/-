"""
regime/regime_detector.py - v5.2.0 FINAL (글로벌 매크로 + KOSPI 융합)
"""

from datetime import datetime, timedelta
from typing import Dict
import logging

from scheduler.macro_collector import get_cached_macro

logger = logging.getLogger(__name__)


class KoreanSpecialFactors:
    @staticmethod
    def is_futures_options_expiry(date: datetime) -> bool:
        first_day = date.replace(day=1)
        days_until_thursday = (3 - first_day.weekday()) % 7
        first_thursday = first_day + timedelta(days=days_until_thursday)
        second_thursday = first_thursday + timedelta(days=7)
        return date.date() == second_thursday.date()

    @staticmethod
    def is_dividend_ex_date(date: datetime) -> bool:
        if date.month == 12:
            last_day = datetime(date.year, 12, 31)
            while last_day.weekday() >= 5:
                last_day -= timedelta(days=1)
            return date.date() == last_day.date()
        return False

    @staticmethod
    def get_program_trading_imbalance(program_buy: float, program_sell: float) -> float:
        if program_buy + program_sell == 0:
            return 0.0
        return (program_buy - program_sell) / (program_buy + program_sell)

    @staticmethod
    def get_foreigner_futures_position(futures_long: float, futures_short: float) -> float:
        if futures_long + futures_short == 0:
            return 0.0
        return (futures_long - futures_short) / (futures_long + futures_short)


class RegimeDetector:
    def __init__(self):
        self.korean = KoreanSpecialFactors()
        self.current_regime = 'Sideways'
        self.current_date = datetime.now()

    def detect(self, data: Dict) -> Dict:
        macro = get_cached_macro()
        if data is None:
            data = {}

        kospi = data.get('kospi_trend', macro.kospi_trend)
        spx = data.get('spx_trend', macro.spx_trend)
        ndx = data.get('ndx_trend', macro.ndx_trend)
        sox = data.get('sox_trend', macro.sox_trend)
        vix = data.get('vix', macro.vix)
        oil = data.get('oil_price', macro.oil_price)
        usdkrw = data.get('usdkrw', macro.usdkrw)

        trend_score = (self._normalize(kospi, -3.0, 3.0) * 0.4 +
                       self._normalize(spx, -3.0, 3.0) * 0.3 +
                       self._normalize(sox, -5.0, 5.0) * 0.3)
        risk_score = 1.0 - self._normalize(vix, 15.0, 35.0)
        oil_score = 1.0 - self._normalize(oil, 60.0, 100.0)
        fx_score = 1.0 - self._normalize(usdkrw, 1250.0, 1400.0)

        special_adjustment = 0.0
        date = data.get('date', self.current_date)
        if self.korean.is_futures_options_expiry(date):
            special_adjustment += 0.1
        if self.korean.is_dividend_ex_date(date):
            special_adjustment += 0.05
        program_imbalance = self.korean.get_program_trading_imbalance(
            data.get('program_buy', 0), data.get('program_sell', 0)
        )
        special_adjustment += program_imbalance * 0.1

        composite = (trend_score * 0.45 + risk_score * 0.30 + oil_score * 0.15 + fx_score * 0.10) + special_adjustment

        if composite >= 0.7:
            regime = 'Bull'
        elif composite >= 0.5:
            regime = 'Sideways'
        elif composite >= 0.3:
            regime = 'Correction'
        elif composite >= 0.1:
            regime = 'Bear'
        else:
            regime = 'Panic'

        self.current_regime = regime
        return {
            'regime': regime,
            'score': composite,
            'components': {'trend': trend_score, 'risk': risk_score, 'oil': oil_score, 'fx': fx_score, 'korean_special': special_adjustment},
            'korean_factors': {
                'futures_options_expiry': self.korean.is_futures_options_expiry(date),
                'dividend_ex_date': self.korean.is_dividend_ex_date(date),
                'program_imbalance': program_imbalance
            },
            'timestamp': datetime.now().isoformat()
        }

    @staticmethod
    def _normalize(value: float, low: float, high: float) -> float:
        if high == low:
            return 0.5
        normalized = (value - low) / (high - low)
        return max(0.0, min(1.0, normalized))