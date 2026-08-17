"""
Regime Detector v5.1.3 — Claude 버그 수정 (Critical)

수정 사항 (v5.1.2 → v5.1.3):
- 🔥 CRITICAL: `_calculate_trend`, `_calculate_risk`, `_calculate_flow`가
  모두 `pass`(암묵적 None 반환) 상태였음. `detect()`에서
  `None * 0.40` 연산이 발생해 규모 국면 판정 자체가 100% 실패(TypeError)했음.
  Regime은 dynamic_weighter, explainer 등 여러 모듈이 의존하므로
  이 버그 하나로 시스템 전체가 마비됨.
- 아래 구현은 기존 파일(macro_filter.py, dynamic_weighter.py)이 사용하는
  데이터 필드(kospi_trend, vix/vkospi, foreigner_net, institution_net,
  program_buy/sell, futures_long/short)와 일관되게 설계함.
  실제 배포 전 파라미터(가중치, 임계값)는 Walk-Forward 검증으로 재조정 필요.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


class KoreanSpecialFactors:
    """한국 시장 특이 요인"""

    @staticmethod
    def is_futures_options_expiry(date: datetime) -> bool:
        """선물옵션 만기일 여부 (매월 두 번째 목요일)"""
        first_day = date.replace(day=1)
        days_until_thursday = (3 - first_day.weekday()) % 7
        first_thursday = first_day + timedelta(days=days_until_thursday)
        second_thursday = first_thursday + timedelta(days=7)
        return date.date() == second_thursday.date()

    @staticmethod
    def is_dividend_ex_date(date: datetime) -> bool:
        """배당락일 여부 (12월 마지막 영업일 — 대표 예시, 종목별 상이)"""
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
    """
    시장 국면 판정 v5.1.3

    Layer 구성:
    1. 시장 방향 (Trend) - 40%
    2. 위험 (Risk) - 30%
    3. 수급 (Flow) - 30%
    + 한국 특이 요인 (선물옵션 만기, 배당락, 프로그램매매, 외국인 선물)

    ⚠️ 아래 임계값·가중치는 초기 추정치이며 미검증 상태.
       Phase 1 Shadow Mode 실측 데이터로 재보정 필요 (Claude 검토 의견 참조).
    """

    def __init__(self):
        self.korean = KoreanSpecialFactors()
        self.current_regime = 'Sideways'
        self.current_date = datetime.now()

    def detect(self, data: Dict) -> Dict:
        """국면 판정 (한국 특이 요인 포함)"""

        trend_score = self._calculate_trend(data)
        risk_score = self._calculate_risk(data)
        flow_score = self._calculate_flow(data)
        special_adjustment = self._calculate_korean_special(data)

        raw_score = (
            trend_score * 0.40 +
            risk_score * 0.30 +
            flow_score * 0.30
        )

        final_score = raw_score + special_adjustment
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

    # ============================================================
    # 🔥 구현 완료: Trend / Risk / Flow (기존 pass → 실제 로직)
    # ============================================================

    def _calculate_trend(self, data: Dict) -> float:
        """
        시장 방향성 점수 (0.0 ~ 1.0)
        - KOSPI 5일 추세 (%)
        - KOSPI 200 대비 20일 이격도
        """
        kospi_trend = data.get('kospi_trend', 0.0)      # % 단위, macro_filter.py와 동일 필드
        ma20_gap = data.get('kospi_ma20_gap', 0.0)       # % 단위, 20일선 대비 괴리율

        # 5일 추세를 -5%~+5% 범위로 정규화 → 0~1
        trend_component = self._normalize(kospi_trend, -5.0, 5.0)
        # 20일선 이격도를 -3%~+3% 범위로 정규화 → 0~1
        ma_component = self._normalize(ma20_gap, -3.0, 3.0)

        return round(trend_component * 0.6 + ma_component * 0.4, 4)

    def _calculate_risk(self, data: Dict) -> float:
        """
        위험 점수 (0.0 ~ 1.0, 높을수록 안전/낮은 위험)
        - VKOSPI (변동성 지수, 낮을수록 안전)
        - USDKRW 급변동 여부
        """
        vkospi = data.get('vkospi', 20.0)
        usdkrw_change = abs(data.get('usdkrw_change_pct', 0.0))  # 당일 변동률 %

        # VKOSPI 15(안전)~40(패닉) 범위를 역방향 정규화 (낮을수록 risk_score 높음=안전)
        vkospi_component = 1.0 - self._normalize(vkospi, 15.0, 40.0)
        # 환율 변동성 0~2% 범위, 클수록 위험 → 역방향
        fx_component = 1.0 - self._normalize(usdkrw_change, 0.0, 2.0)

        return round(vkospi_component * 0.7 + fx_component * 0.3, 4)

    def _calculate_flow(self, data: Dict) -> float:
        """
        수급 점수 (0.0 ~ 1.0)
        - 외국인 순매수, 기관 순매수, 프로그램 매매
        """
        foreigner_net = data.get('foreigner_net', 0.0)     # 억원 단위
        institution_net = data.get('institution_net', 0.0)  # 억원 단위
        program_imbalance = self.korean.get_program_trading_imbalance(
            data.get('program_buy', 0),
            data.get('program_sell', 0)
        )

        foreigner_component = self._normalize(foreigner_net, -3000, 3000)
        institution_component = self._normalize(institution_net, -2000, 2000)
        program_component = self._normalize(program_imbalance, -1.0, 1.0)

        return round(
            foreigner_component * 0.4 +
            institution_component * 0.35 +
            program_component * 0.25,
            4
        )

    @staticmethod
    def _normalize(value: float, low: float, high: float) -> float:
        """value를 [low, high] 구간 기준 0~1로 정규화 (클램핑 포함)"""
        if high == low:
            return 0.5
        normalized = (value - low) / (high - low)
        return max(0.0, min(1.0, normalized))

    # ============================================================
    # 한국 특이 요인 (기존 유지)
    # ============================================================

    def _calculate_korean_special(self, data: Dict) -> float:
        """한국 특이 요인 조정값 계산"""
        adjustment = 0.0
        date = data.get('date', self.current_date)

        if self.korean.is_futures_options_expiry(date):
            adjustment += 0.1
            logger.debug("선물옵션 만기일 감지: 변동성 증가 반영 (+0.1)")

        if self.korean.is_dividend_ex_date(date):
            adjustment += 0.05
            logger.debug("배당락일 감지: 저평가 효과 반영 (+0.05)")

        program_imbalance = self.korean.get_program_trading_imbalance(
            data.get('program_buy', 0),
            data.get('program_sell', 0)
        )
        adjustment += program_imbalance * 0.1

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

    def _map_regime(self, score: float) -> str:
        if score >= 0.7:
            return 'Bull'
        elif score >= 0.5:
            return 'Sideways'
        elif score >= 0.3:
            return 'Correction'
        elif score >= 0.1:
            return 'Bear'
        elif score >= -0.1:
            return 'Panic'
        else:
            return 'Recovery'
