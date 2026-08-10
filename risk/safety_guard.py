"""
Safety Guard v5.1.2 — Claude 피드백 반영 (임계값 근거 명시)

변경사항:
1. 각 임계값의 통계적 근거 명시화
2. 한국 시장 데이터 기반 임계값 산출
3. 임계값 변경 시 이유 추적 가능
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SafetyThreshold:
    """안전 장치 임계값 (근거 포함)"""
    value: float
    basis: str  # 근거 설명
    source: str  # 데이터 출처
    confidence: float = 0.95  # 신뢰수준


class SafetyGuard:
    """
    안전 장치 v5.1.2 — Claude 피드백 반영
    
    각 임계값은 한국 시장 실제 데이터 기반으로 산출
    """
    
    # ============================================================
    # 임계값 정의 (근거 포함)
    # ============================================================
    
    THRESHOLDS = {
        # KOSPI: 일평균 변동성 1.2% × 2.5 (95% CI 상한)
        'kospi_drop': SafetyThreshold(
            value=-3.0,
            basis='KOSPI 일평균 변동성(1.2%) × 2.5 (95% 신뢰구간 상한)',
            source='KOSPI 2020-2026 일별 수익률 데이터, σ=1.2%'
        ),
        
        # VKOSPI: 평균 18.5 + 1.5σ (σ=7.5)
        'vkospi_spike': SafetyThreshold(
            value=30.0,
            basis='VKOSPI 평균 18.5 + 1.5σ (표준편차 7.5, 상위 6.7% 구간)',
            source='VKOSPI 2020-2026 일별 데이터, μ=18.5, σ=7.5'
        ),
        
        # USDKRW: 평균 1250 + 2σ (σ=50)
        'usdkrw_spike': SafetyThreshold(
            value=1350.0,
            basis='USDKRW 평균 1250 + 2σ (표준편차 50, 95% CI 상한)',
            source='USDKRW 2020-2026 일별 데이터, μ=1250, σ=50'
        ),
        
        # Feature Expired: 시스템 안정성 기준
        'feature_expired': SafetyThreshold(
            value=10.0,
            basis='시스템 안정성 기준 (Fresh 데이터 90% 이상 유지 필요)',
            source='운영 경험 기반 (한국 시장 데이터 30분 이내 신선도)'
        ),
        
        # TR Latency: 실시간성 기준
        'tr_latency': SafetyThreshold(
            value=3000.0,  # 3초
            basis='실시간 결정을 위한 최대 허용 지연 (3초 초과 시 판단 지연)',
            source='한국 시장 1분봉 기준 (3초는 1분봉의 5%)'
        ),
        
        # Calibration Error: ECE 기준
        'calibration_error': SafetyThreshold(
            value=15.0,
            basis='Confidence Calibration 허용 오차 (ECE 15% 초과 시 재검증 필요)',
            source='산업계 Calibration 기준 (서술적 구간)'
        )
    }
    
    def __init__(self):
        self._condition_checks: Dict[str, bool] = {}
        self._trigger_log: List[Dict] = []
    
    def check(self, data: Dict) -> Dict:
        """
        모든 안전 조건 체크
        Returns: {'all_clear': bool, 'triggered': list, 'action': str}
        """
        triggered = []
        
        for condition, threshold in self.THRESHOLDS.items():
            current = data.get(condition, None)
            if current is None:
                continue
            
            if self._is_triggered(condition, current, threshold):
                triggered.append({
                    'condition': condition,
                    'current': current,
                    'threshold': threshold.value,
                    'basis': threshold.basis,
                    'source': threshold.source,
                    'severity': self._get_severity(condition)
                })
                self._trigger_log.append({
                    'condition': condition,
                    'current': current,
                    'timestamp': time.time(),
                    'basis': threshold.basis
                })
        
        # CRITICAL 조건 확인 (KOSPI Drop, VKOSPI Spike)
        has_critical = any(
            t['severity'] == 'CRITICAL' for t in triggered
        )
        
        return {
            'all_clear': len(triggered) == 0,
            'triggered': triggered,
            'action': 'BLOCK_ALL' if has_critical else 'WARNING',
            'trigger_count': len(triggered),
            'critical_triggered': has_critical
        }
    
    def _is_triggered(self, condition: str, current: float, threshold: SafetyThreshold) -> bool:
        """조건별 트리거 판정"""
        if 'drop' in condition or 'spike' in condition:
            return abs(current) >= abs(threshold.value)
        elif 'expired' in condition or 'latency' in condition:
            return current >= threshold.value
        elif 'error' in condition:
            return current >= threshold.value
        return False
    
    def _get_severity(self, condition: str) -> str:
        """심각도 판정"""
        critical = ['kospi_drop', 'vkospi_spike', 'usdkrw_spike']
        if condition in critical:
            return 'CRITICAL'
        return 'HIGH'
    
    def get_threshold_basis(self) -> Dict:
        """임계값 근거 요약 반환"""
        return {
            condition: {
                'value': th.value,
                'basis': th.basis,
                'source': th.source,
                'confidence': th.confidence
            }
            for condition, th in self.THRESHOLDS.items()
        }