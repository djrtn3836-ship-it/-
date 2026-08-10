"""
Regime Detector v5.1.2 — Claude 피드백 반영 (한국 특이 요인 추가)

변경사항:
1. 선물옵션 만기일 자동 인식
2. 배당락일 자동 인식
3. 프로그램 매매 불균형 반영
4. 외국인 선물 포지션 반영
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


class KoreanSpecialFactors:
    """한국 시장 특이 요인"""
    
    # ===== 한국 시장 특이 일정 =====
    @staticmethod
    def is_futures_options_expiry(date: datetime) -> bool:
        """선물옵션 만기일 여부 (매월 두 번째 목요일)"""
        # 매월 두 번째 목요일 계산
        first_day = date.replace(day=1)
        # 첫 번째 목요일 찾기
        days_until_thursday = (3 - first_day.weekday()) % 7
        first_thursday = first_day + timedelta(days=days_until_thursday)
        second_thursday = first_thursday + timedelta(days=7)
        
        # 두 번째 목요일이 해당 날짜인지
        return date.date() == second_thursday.date()
    
    @staticmethod
    def is_dividend_ex_date(date: datetime) -> bool:
        """배당락일 여부 (분기말 12월 마지막 영업일)"""
        # 실제로는 각 종목별 배당락일이 다름
        # 여기서는 대표적으로 12월 말일 기준
        if date.month == 12:
            # 12월 마지막 영업일
            last_day = datetime(date.year, 12, 31)
            while last_day.weekday() >= 5:  # 주말이면 하루 전
                last_day -= timedelta(days=1)
            return date.date() == last_day.date()
        return False
    
    @staticmethod
    def get_program_trading_imbalance(program_buy: float, program_sell: float) -> float:
        """프로그램 매매 불균형"""
        if program_buy + program_sell == 0:
            return 0.0
        return (program_buy - program_sell) / (program_buy + program_sell)
    
    @staticmethod
    def get_foreigner_futures_position(futures_long: float, futures_short: float) -> float:
        """외국인 선물 포지션"""
        if futures_long + futures_short == 0:
            return 0.0
        return (futures_long - futures_short) / (futures_long + futures_short)


class RegimeDetector:
    """
    시장 국면 판정 v5.1.2 — 한국 특이 요인 포함
    
    Layer 구성:
    1. 시장 방향 (Trend) - 40%
    2. 위험 (Risk) - 30%
    3. 수급 (Flow) - 30%
    
    추가: 한국 특이 요인 (선물옵션 만기, 배당락, 프로그램매매, 외국인 선물)
    """
    
    def __init__(self):
        self.korean = KoreanSpecialFactors()
        self.current_regime = 'Sideways'
        self.current_date = datetime.now()
    
    def detect(self, data: Dict) -> Dict:
        """국면 판정 (한국 특이 요인 포함)"""
        
        # 1. Layer 1: 시장 방향 (Trend)
        trend_score = self._calculate_trend(data)
        
        # 2. Layer 2: 위험 (Risk)
        risk_score = self._calculate_risk(data)
        
        # 3. Layer 3: 수급 (Flow)
        flow_score = self._calculate_flow(data)
        
        # 4. 한국 특이 요인 (Adjustment)
        special_adjustment = self._calculate_korean_special(data)
        
        # 5. 종합 점수
        raw_score = (
            trend_score * 0.40 +
            risk_score * 0.30 +
            flow_score * 0.30
        )
        
        # 특이 요인 조정
        final_score = raw_score + special_adjustment
        
        # 6. 국면 매핑
        regime = self._map_regime(final_score)
        self.current_regime = regime
        
        return {
            'regime': regime,
            'score': final_score,
            'components': {
                'trend': trend_score,
                'risk': risk_score,
                'flow': flow_score,
                'korean_special': special_adjustment
            },
            'korean_factors': self._get_korean_factor_status(data),
            'timestamp': datetime.now().isoformat()
        }
    
    def _calculate_korean_special(self, data: Dict) -> float:
        """한국 특이 요인 조정값 계산"""
        adjustment = 0.0
        date = data.get('date', self.current_date)
        
        # 1. 선물옵션 만기일: 변동성 증가 → Risk Score 상승
        if self.korean.is_futures_options_expiry(date):
            adjustment += 0.1
            logger.debug(f"선물옵션 만기일 감지: 변동성 증가 반영 (+0.1)")
        
        # 2. 배당락일: 저평가 효과 → Trend Score 상승
        if self.korean.is_dividend_ex_date(date):
            adjustment += 0.05
            logger.debug(f"배당락일 감지: 저평가 효과 반영 (+0.05)")
        
        # 3. 프로그램 매매 불균형
        program_imbalance = self.korean.get_program_trading_imbalance(
            data.get('program_buy', 0),
            data.get('program_sell', 0)
        )
        adjustment += program_imbalance * 0.1
        
        # 4. 외국인 선물 포지션
        foreigner_futures = self.korean.get_foreigner_futures_position(
            data.get('futures_long', 0),
            data.get('futures_short', 0)
        )
        adjustment += foreigner_futures * 0.1
        
        return adjustment
    
    def _get_korean_factor_status(self, data: Dict) -> Dict:
        """한국 특이 요인 상태 반환"""
        date = data.get('date', self.current_date)
        
        return {
            'futures_options_expiry': self.korean.is_futures_options_expiry(date),
            'dividend_ex_date': self.korean.is_dividend_ex_date(date),
            'program_trading_imbalance': self.korean.get_program_trading_imbalance(
                data.get('program_buy', 0),
                data.get('program_sell', 0)
            ),
            'foreigner_futures_position': self.korean.get_foreigner_futures_position(
                data.get('futures_long', 0),
                data.get('futures_short', 0)
            ),
            'vix': data.get('vix', 20),
            'vkospi': data.get('vkospi', 20)
        }
    
    def _calculate_trend(self, data: Dict) -> float:
        # (기존 구현 유지)
        pass
    
    def _calculate_risk(self, data: Dict) -> float:
        # (기존 구현 유지)
        pass
    
    def _calculate_flow(self, data: Dict) -> float:
        # (기존 구현 유지)
        pass
    
    def _map_regime(self, score: float) -> str:
        # (기존 구현 유지)
        if score >= 0.7: return 'Bull'
        elif score >= 0.5: return 'Sideways'
        elif score >= 0.3: return 'Correction'
        elif score >= 0.1: return 'Bear'
        elif score >= -0.1: return 'Panic'
        else: return 'Recovery'