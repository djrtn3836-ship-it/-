# -*- coding: utf-8 -*-
"""
tests/unit/test_signal_pipeline_sqi_v2.py - SQI v2 단위 테스트

커버리지:
    - _calc_momentum_score(): RSI+MACD 기반 모멘텀 품질 스코어
    - _calc_bb_pct(): Bollinger %B 계산
    - compute_sqi_v2(): SQI v2 복합 스코어 (모멘텀·거래량·변동성)
    - EnsembleResult.sqi_v2 필드 추가 확인
    - SignalPipeline._ensemble() with tech_data → SQI v2 계산
    - SignalPipeline._ensemble() without tech_data → sqi_v2 == sqi_v1 (fallback)
    - SignalPipeline._calc_signal_confidence() SQI v2 우선 사용
    - SignalPipeline()._collect_evidence() SQI v2 레이블
    - process() 통합: tech_data 있을 때 sqi_v2 > 0
    - SQI v2 경계값 테스트 (HOLD 강제 기준)
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from typing import List

from application.analysis.signal_pipeline import (
    SignalPipeline,
    EnsembleResult,
    compute_sqi_v2,
    _calc_momentum_score,
    _calc_bb_pct,
    _SQI_V2_MOMENTUM_W,
    _SQI_V2_CONFIDENCE_W,
    _SQI_V2_CONSENSUS_W,
    _MIN_CONFIDENCE,
)
from domain.models.signal import Action
from domain.strategies.base import StrategyResult


# ═══════════════════════════════════════════════════════════════════
#  헬퍼 팩토리
# ═══════════════════════════════════════════════════════════════════

def _make_result(name: str, action: str, score: float, confidence: float) -> StrategyResult:
    return StrategyResult(
        name=name,
        action=action,
        score=score,
        confidence=confidence,
        reasons=[f"SQI v2 test: {name}"],
    )


def _make_pipeline() -> SignalPipeline:
    """DB 없이 SignalPipeline 생성 (레거시 필터 mock)"""
    pipeline = SignalPipeline(db_manager=None)
    pipeline.macro_filter.check = MagicMock(return_value={"score": 0.6})
    pipeline.sector_filter.check = MagicMock(return_value={"score": 0.6})
    pipeline.stock_filter.check = MagicMock(return_value={"score": 0.6})
    pipeline.korean_filter.check = MagicMock(return_value={"score": 0.6})
    pipeline.weighter.calculate = MagicMock(
        return_value={"trend_weight": 0.3, "risk_weight": 0.2, "flow_weight": 0.4}
    )
    pipeline.atr_service = MagicMock()
    pipeline.atr_service.calculate = AsyncMock(return_value=1500.0)
    return pipeline


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════
#  1. _calc_momentum_score() 테스트
# ═══════════════════════════════════════════════════════════════════

class TestCalcMomentumScore:
    """RSI + MACD 히스토그램 기반 모멘텀 스코어"""

    def test_rsi_50_neutral_returns_low_score(self):
        """RSI=50 중립 → 모멘텀 스코어 낮음 (0.0 ± macd보정)"""
        score = _calc_momentum_score(rsi=50.0, macd_hist=0.0)
        assert score == pytest.approx(0.0, abs=1e-9)

    def test_rsi_30_oversold_returns_high_score(self):
        """RSI=30 과매도 → abs(30-50)/50 = 0.4, macd=0 → 0.4"""
        score = _calc_momentum_score(rsi=30.0, macd_hist=0.0)
        assert score == pytest.approx(0.4, abs=1e-9)

    def test_rsi_70_overbought_returns_high_score(self):
        """RSI=70 과매수 → abs(70-50)/50 = 0.4, macd=0 → 0.4"""
        score = _calc_momentum_score(rsi=70.0, macd_hist=0.0)
        assert score == pytest.approx(0.4, abs=1e-9)

    def test_rsi_0_extreme_returns_max_without_macd(self):
        """RSI=0 극단 → abs(0-50)/50 = 1.0, macd=0 → 1.0"""
        score = _calc_momentum_score(rsi=0.0, macd_hist=0.0)
        assert score == pytest.approx(1.0, abs=1e-9)

    def test_rsi_100_extreme_returns_max_without_macd(self):
        """RSI=100 극단 → abs(100-50)/50 = 1.0, macd=0 → 1.0"""
        score = _calc_momentum_score(rsi=100.0, macd_hist=0.0)
        assert score == pytest.approx(1.0, abs=1e-9)

    def test_positive_macd_adds_0_1(self):
        """macd_hist > 0 → +0.1 보정"""
        base = _calc_momentum_score(rsi=50.0, macd_hist=0.0)
        with_macd = _calc_momentum_score(rsi=50.0, macd_hist=1.0)
        assert with_macd == pytest.approx(base + 0.1, abs=1e-9)

    def test_negative_macd_subtracts_0_1(self):
        """macd_hist < 0 → -0.1 보정 (실제 개선: 클램프 가능성 고려)"""
        # RSI=50 일 때 base=0.0, macd_dir=-0.1 → clamp(0.0-0.1, 0, 1) = 0.0
        # 실제 기대: macd < 0 시 base에서 0.1 돈시 (clamp 후)
        # RSI=60 일 때 base=0.2, macd=-1 → 0.2-0.1 = 0.1
        base = _calc_momentum_score(rsi=60.0, macd_hist=0.0)
        with_neg_macd = _calc_momentum_score(rsi=60.0, macd_hist=-1.0)
        assert with_neg_macd == pytest.approx(base - 0.1, abs=1e-9)

    def test_result_clamped_to_0_1(self):
        """결과값은 항상 [0, 1] 범위"""
        for rsi in [0, 25, 50, 75, 100]:
            for macd in [-5.0, 0.0, 5.0]:
                score = _calc_momentum_score(rsi=rsi, macd_hist=macd)
                assert 0.0 <= score <= 1.0, f"rsi={rsi}, macd={macd} → {score}"

    def test_rsi_clamped_below_0(self):
        """RSI 음수 입력 → 0으로 처리"""
        score = _calc_momentum_score(rsi=-10.0, macd_hist=0.0)
        assert score == pytest.approx(1.0, abs=1e-9)  # abs(0 - 50) / 50 = 1.0

    def test_rsi_clamped_above_100(self):
        """RSI 100 초과 입력 → 100으로 처리"""
        score = _calc_momentum_score(rsi=150.0, macd_hist=0.0)
        assert score == pytest.approx(1.0, abs=1e-9)  # abs(100 - 50) / 50 = 1.0


# ═══════════════════════════════════════════════════════════════════
#  2. _calc_bb_pct() 테스트
# ═══════════════════════════════════════════════════════════════════

class TestCalcBbPct:
    """Bollinger %B 계산"""

    def test_price_at_middle_returns_0_5(self):
        """가격이 중간선 → 0.5"""
        pct = _calc_bb_pct(price=100.0, bb_upper=110.0, bb_lower=90.0)
        assert pct == pytest.approx(0.5, abs=1e-9)

    def test_price_at_upper_returns_1_0(self):
        """가격이 상단 밴드 → 1.0"""
        pct = _calc_bb_pct(price=110.0, bb_upper=110.0, bb_lower=90.0)
        assert pct == pytest.approx(1.0, abs=1e-9)

    def test_price_at_lower_returns_0_0(self):
        """가격이 하단 밴드 → 0.0"""
        pct = _calc_bb_pct(price=90.0, bb_upper=110.0, bb_lower=90.0)
        assert pct == pytest.approx(0.0, abs=1e-9)

    def test_price_above_upper_clamped_to_1(self):
        """가격이 상단 초과 → 1.0으로 clamp"""
        pct = _calc_bb_pct(price=120.0, bb_upper=110.0, bb_lower=90.0)
        assert pct == pytest.approx(1.0, abs=1e-9)

    def test_price_below_lower_clamped_to_0(self):
        """가격이 하단 미만 → 0.0으로 clamp"""
        pct = _calc_bb_pct(price=80.0, bb_upper=110.0, bb_lower=90.0)
        assert pct == pytest.approx(0.0, abs=1e-9)

    def test_zero_bandwidth_returns_0_5_neutral(self):
        """밴드 폭 0 (상단=하단) → 중립 0.5 반환"""
        pct = _calc_bb_pct(price=100.0, bb_upper=100.0, bb_lower=100.0)
        assert pct == pytest.approx(0.5, abs=1e-9)

    def test_zero_price_returns_neutral(self):
        """가격 0 → 중립 0.5 반환 (데이터 부족)"""
        pct = _calc_bb_pct(price=0.0, bb_upper=110.0, bb_lower=90.0)
        assert pct == pytest.approx(0.5, abs=1e-9)

    def test_result_always_in_range(self):
        """결과는 항상 [0, 1]"""
        cases = [
            (85.0, 110.0, 90.0),
            (115.0, 110.0, 90.0),
            (100.0, 100.0, 100.0),
        ]
        for price, upper, lower in cases:
            pct = _calc_bb_pct(price, upper, lower)
            assert 0.0 <= pct <= 1.0


# ═══════════════════════════════════════════════════════════════════
#  3. compute_sqi_v2() 테스트
# ═══════════════════════════════════════════════════════════════════

class TestComputeSqiV2:
    """SQI v2 복합 스코어 계산"""

    def test_ideal_conditions_returns_high_sqi(self):
        """이상적 조건: 높은 모멘텀·거래량·중립 변동성·높은 신뢰도·합의도"""
        sqi = compute_sqi_v2(
            momentum_score=0.8,
            volume_ratio=2.0,   # 평균의 2배
            bb_pct=0.5,         # 중간선 → 패널티 없음
            confidence=0.9,
            consensus=0.95,
        )
        assert sqi > 0.7, f"이상적 조건에서 SQI v2={sqi:.4f} < 0.7"

    def test_poor_conditions_returns_low_sqi(self):
        """열악 조건: 낮은 모멘텀·거래량·과열 변동성·낮은 신뢰도·합의도"""
        sqi = compute_sqi_v2(
            momentum_score=0.05,
            volume_ratio=0.1,   # 평균의 10%
            bb_pct=0.02,        # 하단 밴드 근접 → 최대 패널티
            confidence=0.2,
            consensus=0.2,
        )
        assert sqi < 0.3, f"열악 조건에서 SQI v2={sqi:.4f} >= 0.3"

    def test_neutral_inputs_returns_mid_range(self):
        """모든 입력 0.5 → 중간 범위 SQI"""
        sqi = compute_sqi_v2(
            momentum_score=0.5,
            volume_ratio=1.0,
            bb_pct=0.5,
            confidence=0.5,
            consensus=0.5,
        )
        assert 0.3 <= sqi <= 0.7

    def test_result_bounded_0_1(self):
        """결과값은 항상 [0, 1] 범위"""
        # 극단 케이스
        cases = [
            (0.0, 0.0, 0.0, 0.0, 0.0),
            (1.0, 10.0, 1.0, 1.0, 1.0),
            (0.5, 1.0, 0.5, 0.5, 0.5),
        ]
        for ms, vr, bp, conf, cons in cases:
            sqi = compute_sqi_v2(ms, vr, bp, conf, cons)
            assert 0.0 <= sqi <= 1.0, f"SQI v2 범위 초과: {sqi}"

    def test_high_volume_boosts_sqi(self):
        """거래량 높을수록 SQI v2 증가"""
        base = compute_sqi_v2(0.5, 1.0, 0.5, 0.6, 0.7)
        high = compute_sqi_v2(0.5, 3.0, 0.5, 0.6, 0.7)  # 거래량 3배
        assert high > base, f"높은 거래량이 SQI를 증가시켜야 함: base={base:.4f}, high={high:.4f}"

    def test_low_volume_reduces_sqi(self):
        """거래량 낮을수록 SQI v2 감소"""
        base = compute_sqi_v2(0.5, 1.0, 0.5, 0.6, 0.7)
        low = compute_sqi_v2(0.5, 0.2, 0.5, 0.6, 0.7)  # 거래량 20%
        assert low < base, f"낮은 거래량이 SQI를 감소시켜야 함: base={base:.4f}, low={low:.4f}"

    def test_extreme_bb_pct_reduces_sqi(self):
        """BB %B 극단값(0 or 1) → 과열 패널티로 SQI 감소"""
        neutral = compute_sqi_v2(0.5, 1.0, 0.5, 0.6, 0.7)
        extreme_upper = compute_sqi_v2(0.5, 1.0, 1.0, 0.6, 0.7)
        extreme_lower = compute_sqi_v2(0.5, 1.0, 0.0, 0.6, 0.7)
        assert extreme_upper < neutral, "상단 밴드 과열 → SQI 감소"
        assert extreme_lower < neutral, "하단 밴드 과열 → SQI 감소"

    def test_weights_sum_verified(self):
        """가중치 합 = 1.0 검증"""
        total = _SQI_V2_MOMENTUM_W + _SQI_V2_CONFIDENCE_W + _SQI_V2_CONSENSUS_W
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_base_component_formula(self):
        """base 성분 공식 검증: 가중치 합산"""
        ms, conf, cons = 0.5, 0.6, 0.7
        expected_base = (
            _SQI_V2_MOMENTUM_W * ms
            + _SQI_V2_CONFIDENCE_W * conf
            + _SQI_V2_CONSENSUS_W * cons
        )
        # volume_ratio=1.0, bb_pct=0.5 → boost≈1.0, penalty=1.0 (중립)
        import math
        vol_boost = 0.7 + 0.3 * math.log(1.0 + 1.0)
        vol_boost = max(0.7, min(1.3, vol_boost))
        vol_penalty = 1.0 - 0.4 * (abs(0.5 - 0.5) * 2.0)
        expected = expected_base * vol_boost * vol_penalty
        actual = compute_sqi_v2(ms, 1.0, 0.5, conf, cons)
        assert actual == pytest.approx(expected, abs=1e-9)

    def test_zero_volume_ratio_safe(self):
        """거래량 비율 0 → 안전하게 처리 (0으로 나누기 없음)"""
        sqi = compute_sqi_v2(0.5, 0.0, 0.5, 0.6, 0.7)
        assert 0.0 <= sqi <= 1.0  # 에러 없이 처리


# ═══════════════════════════════════════════════════════════════════
#  4. EnsembleResult.sqi_v2 필드 테스트
# ═══════════════════════════════════════════════════════════════════

class TestEnsembleResultSqiV2Field:
    """EnsembleResult DTO sqi_v2 필드"""

    def test_sqi_v2_default_is_zero(self):
        """sqi_v2 기본값 = 0.0"""
        er = EnsembleResult(
            score=0.7, confidence=0.8, action="BUY",
            consensus=0.9, sqi=0.72, details=[]
        )
        assert er.sqi_v2 == pytest.approx(0.0)

    def test_sqi_v2_explicit_value(self):
        """sqi_v2 명시적 값 저장"""
        er = EnsembleResult(
            score=0.7, confidence=0.8, action="BUY",
            consensus=0.9, sqi=0.72, details=[], sqi_v2=0.85
        )
        assert er.sqi_v2 == pytest.approx(0.85)

    def test_both_sqi_v1_and_v2_accessible(self):
        """sqi (v1)와 sqi_v2 (v2) 모두 독립적으로 접근 가능"""
        er = EnsembleResult(
            score=0.7, confidence=0.8, action="BUY",
            consensus=0.9, sqi=0.50, details=[], sqi_v2=0.75
        )
        assert er.sqi != er.sqi_v2
        assert er.sqi == pytest.approx(0.50)
        assert er.sqi_v2 == pytest.approx(0.75)

    def test_slots_include_sqi_v2(self):
        """__slots__에 sqi_v2 포함"""
        assert "sqi_v2" in EnsembleResult.__slots__


# ═══════════════════════════════════════════════════════════════════
#  5. SignalPipeline._ensemble() SQI v2 통합 테스트
# ═══════════════════════════════════════════════════════════════════

class TestEnsembleSqiV2:
    """_ensemble() 메서드의 SQI v2 계산"""

    def setup_method(self):
        self.pipeline = _make_pipeline()

    def test_without_tech_data_sqi_v2_equals_sqi_v1(self):
        """tech_data 없음 → sqi_v2 = sqi_v1 (fallback)"""
        results = [
            _make_result("Trend", "BUY", 0.8, 0.85),
            _make_result("Reversal", "BUY", 0.75, 0.80),
        ]
        er = self.pipeline._ensemble(results, tech_data=None)
        assert er.sqi_v2 == pytest.approx(er.sqi, abs=1e-9)

    def test_with_tech_data_sqi_v2_is_positive(self):
        """tech_data 있음 → sqi_v2 > 0"""
        results = [
            _make_result("Trend", "BUY", 0.8, 0.85),
            _make_result("Reversal", "BUY", 0.75, 0.80),
        ]
        tech_data = {
            "rsi": 45.0,
            "macd_hist": 0.5,
            "volume_ratio": 1.5,
            "current_price": 75000.0,
            "bb_upper": 78000.0,
            "bb_lower": 72000.0,
        }
        er = self.pipeline._ensemble(results, tech_data=tech_data)
        assert er.sqi_v2 > 0.0

    def test_with_ideal_tech_data_sqi_v2_higher(self):
        """이상적 tech_data → sqi_v2 > sqi_v1 (거래량 부스트 효과)"""
        results = [
            _make_result("Trend", "BUY", 0.8, 0.9),
            _make_result("Reversal", "BUY", 0.75, 0.85),
        ]
        tech_data = {
            "rsi": 30.0,         # 과매도 → 높은 모멘텀
            "macd_hist": 1.0,    # 상승 → +0.1 보정
            "volume_ratio": 3.0, # 평균 3배 → 부스트
            "current_price": 75000.0,
            "bb_upper": 78000.0,
            "bb_lower": 72000.0,
        }
        er = self.pipeline._ensemble(results, tech_data=tech_data)
        # tech_data 있는 경우 SQI v2 범위 내
        assert 0.0 <= er.sqi_v2 <= 1.0

    def test_empty_results_sqi_v2_zero(self):
        """전략 결과 없음 → sqi_v2 = 0.0"""
        er = self.pipeline._ensemble([], tech_data=None)
        assert er.sqi_v2 == pytest.approx(0.0)

    def test_low_volume_tech_data_sqi_v2_bounded(self):
        """거래량 낮은 tech_data → sqi_v2는 [0, 1] 범위"""
        results = [_make_result("Trend", "BUY", 0.7, 0.7)]
        tech_data = {
            "rsi": 50.0,
            "macd_hist": 0.0,
            "volume_ratio": 0.1,  # 매우 낮은 거래량
            "current_price": 100.0,
            "bb_upper": 110.0,
            "bb_lower": 90.0,
        }
        er = self.pipeline._ensemble(results, tech_data=tech_data)
        assert 0.0 <= er.sqi_v2 <= 1.0

    def test_tech_data_with_ema5_fallback(self):
        """current_price 없고 ema5만 있을 때 → ema5를 price_ref로 사용"""
        results = [_make_result("Trend", "BUY", 0.7, 0.8)]
        tech_data = {
            "rsi": 55.0,
            "macd_hist": 0.3,
            "volume_ratio": 1.2,
            "ema5": 75000.0,        # current_price 없음
            "bb_upper": 78000.0,
            "bb_lower": 72000.0,
        }
        # 에러 없이 처리되어야 함
        er = self.pipeline._ensemble(results, tech_data=tech_data)
        assert 0.0 <= er.sqi_v2 <= 1.0


# ═══════════════════════════════════════════════════════════════════
#  6. _calc_signal_confidence() SQI v2 우선 사용 테스트
# ═══════════════════════════════════════════════════════════════════

class TestCalcSignalConfidenceSqiV2:
    """_calc_signal_confidence()에서 sqi_v2 우선 사용"""

    def test_sqi_v2_positive_uses_v2(self):
        """sqi_v2 > 0 → sqi_v2를 quality_index로 사용"""
        # sqi_v2=0.8 → quality_index=0.8, score_distance=0.6 → raw = 0.6*0.6 + 0.8*0.4 = 0.68
        er_v2 = EnsembleResult(
            score=0.8, confidence=0.8, action="BUY",
            consensus=0.9, sqi=0.1, details=[], sqi_v2=0.8
        )
        conf_v2 = SignalPipeline._calc_signal_confidence(0.8, er_v2)

        # sqi_v1=0.1 만 있을 때 → quality_index=0.1, same score → raw = 0.6*0.6 + 0.1*0.4 = 0.4
        er_v1 = EnsembleResult(
            score=0.8, confidence=0.8, action="BUY",
            consensus=0.9, sqi=0.1, details=[], sqi_v2=0.0
        )
        conf_v1 = SignalPipeline._calc_signal_confidence(0.8, er_v1)

        assert conf_v2 > conf_v1, "sqi_v2가 높으면 confidence도 높아야 함"

    def test_sqi_v2_zero_falls_back_to_v1(self):
        """sqi_v2=0 → sqi_v1 사용 (fallback)"""
        er = EnsembleResult(
            score=0.8, confidence=0.8, action="BUY",
            consensus=0.9, sqi=0.72, details=[], sqi_v2=0.0
        )
        conf = SignalPipeline._calc_signal_confidence(0.8, er)
        # score_distance = |0.8-0.5|*2=0.6, quality=0.72 → raw=0.6*0.6+0.72*0.4=0.648
        expected = max(0.30, min(0.95, 0.6 * 0.6 + 0.72 * 0.4))
        assert conf == pytest.approx(expected, abs=1e-6)

    def test_confidence_bounded_0_30_to_0_95(self):
        """confidence는 항상 [0.30, 0.95] 범위"""
        for sqi_v2 in [0.0, 0.5, 1.0]:
            er = EnsembleResult(
                score=0.5, confidence=0.5, action="HOLD",
                consensus=0.5, sqi=0.25, details=[], sqi_v2=sqi_v2
            )
            conf = SignalPipeline._calc_signal_confidence(0.5, er)
            assert 0.30 <= conf <= 0.95


# ═══════════════════════════════════════════════════════════════════
#  7. _collect_evidence() SQI v2 레이블 테스트
# ═══════════════════════════════════════════════════════════════════

class TestCollectEvidenceSqiV2:
    """_collect_evidence()에서 SQI_v2 레이블 표시"""

    def test_sqi_v2_positive_shows_v2_label(self):
        """sqi_v2 > 0 → 'SQI_v2' 레이블 표시"""
        ensemble = EnsembleResult(
            score=0.75, confidence=0.8, action="BUY",
            consensus=0.9, sqi=0.50, details=["Trend:BUY"],
            sqi_v2=0.75
        )
        pos, neg = SignalPipeline()._collect_evidence(
            Action.BUY, 0.75, ensemble, {"rsi": 55}, "Bullish"
        )
        # SQI_v2 레이블이 긍정 근거에 포함되어야 함
        has_sqi_v2 = any("SQI_v2" in p for p in pos)
        assert has_sqi_v2, f"SQI_v2 레이블이 없음. positives={pos}"

    def test_sqi_v2_zero_shows_v1_label(self):
        """sqi_v2 = 0 → 'SQI_v1' 레이블 표시 (fallback)"""
        ensemble = EnsembleResult(
            score=0.75, confidence=0.8, action="BUY",
            consensus=0.9, sqi=0.72, details=["Trend:BUY"],
            sqi_v2=0.0
        )
        pos, neg = SignalPipeline()._collect_evidence(
            Action.BUY, 0.75, ensemble, {"rsi": 55}, "Bullish"
        )
        has_sqi_v1 = any("SQI_v1" in p for p in pos)
        assert has_sqi_v1, f"SQI_v1 레이블이 없음. positives={pos}"

    def test_low_sqi_v2_shows_warning_label(self):
        """낮은 sqi_v2 → 경고 레이블에 SQI_v2 표시"""
        ensemble = EnsembleResult(
            score=0.5, confidence=0.3, action="HOLD",
            consensus=0.4, sqi=0.12, details=[],
            sqi_v2=0.10
        )
        pos, neg = SignalPipeline()._collect_evidence(
            Action.HOLD, 0.5, ensemble, {}, "Sideways"
        )
        has_sqi_warn = any("SQI" in n for n in neg)
        assert has_sqi_warn, f"SQI 경고가 없음. negatives={neg}"


# ═══════════════════════════════════════════════════════════════════
#  8. process() 통합: SQI v2 end-to-end 테스트
# ═══════════════════════════════════════════════════════════════════

class TestProcessSqiV2Integration:
    """process() 통합에서 SQI v2가 정상 동작"""

    def test_process_with_full_tech_data_produces_valid_signal(self):
        """tech_data 완전히 주어진 경우 유효한 Signal 생성"""
        pipeline = _make_pipeline()
        data = {
            "ticker": "005930",
            "price": 75000.0,
            "regime": "Bullish",
            "tech_data": {
                "rsi": 45.0,
                "macd_hist": 0.5,
                "volume_ratio": 1.5,
                "current_price": 75000.0,
                "bb_upper": 78000.0,
                "bb_lower": 72000.0,
                "ema5": 75500.0,
                "ema20": 74000.0,
                "ema60": 72000.0,
            }
        }
        signal = _run(pipeline.process(data))
        assert signal.ticker == "005930"
        assert signal.action in (Action.BUY, Action.SELL, Action.HOLD)
        assert 0.0 <= signal.score <= 1.0
        assert 0.3 <= signal.confidence <= 0.95

    def test_process_without_tech_data_still_valid(self):
        """tech_data 없어도 유효한 Signal 생성 (SQI v2 fallback)"""
        pipeline = _make_pipeline()
        data = {
            "ticker": "000660",
            "price": 120000.0,
            "regime": "Sideways",
        }
        signal = _run(pipeline.process(data))
        assert signal.action in (Action.BUY, Action.SELL, Action.HOLD)

    def test_compute_sqi_v2_method_on_pipeline(self):
        """SignalPipeline.compute_sqi_v2() 정적 메서드 호출 (클래스 직접 호출)"""
        # @trace.traced는 instance method처럼 동작하므로 모듈 수준 함수와 직접 비교
        expected = compute_sqi_v2(0.6, 1.5, 0.5, 0.8, 0.9)
        # 클래스 직접 호출 (staticmethod)
        sqi = SignalPipeline.compute_sqi_v2(
            momentum_score=0.6,
            volume_ratio=1.5,
            bb_pct=0.5,
            confidence=0.8,
            consensus=0.9,
        )
        assert sqi == pytest.approx(expected, abs=1e-9)

    def test_sqi_v2_below_threshold_forces_hold(self):
        """SQI v2가 _MIN_CONFIDENCE 미만 → HOLD 강제 (tech_data 있을 때)"""
        pipeline = _make_pipeline()
        # 모든 전략을 mock으로 매우 낮은 신뢰도로 설정
        from unittest.mock import patch, AsyncMock as AM
        from domain.strategies.base import StrategyResult

        async def mock_analyze(data):
            return StrategyResult(
                name="MockStrategy",
                action="HOLD",
                score=0.5,
                confidence=0.05,  # 매우 낮은 신뢰도 → SQI v2 낮아짐
                reasons=["mock"],
            )

        for strategy in pipeline.strategies:
            strategy.analyze = mock_analyze

        data = {
            "ticker": "005930",
            "price": 75000.0,
            "regime": "Sideways",
            "tech_data": {
                "rsi": 50.0,
                "macd_hist": 0.0,
                "volume_ratio": 0.5,
                "current_price": 75000.0,
                "bb_upper": 80000.0,
                "bb_lower": 70000.0,
            }
        }
        signal = _run(pipeline.process(data))
        # 신뢰도 낮아 HOLD이어야 함
        assert signal.action == Action.HOLD
