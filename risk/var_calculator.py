"""
VaR Calculator v5.1.2 — Claude 피드백 반영 (Modified VaR 적용)

변경사항:
1. 정규분포 가정 → Modified VaR (Cornish-Fisher Expansion)
2. 한국 시장 팻테일(Fat-tail) 현상 반영
3. Historical VaR도 함께 제공
"""

import numpy as np
from scipy.stats import norm
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class VaRCalculator:
    """
    VaR 계산기 v5.1.2
    
    정규분포 가정 대신 Modified VaR (Cornish-Fisher) 적용
    한국 시장의 팻테일(Fat-tail) 현상을 반영
    """
    
    def __init__(self, confidence: float = 0.95, window: int = 252):
        self.confidence = confidence
        self.window = window  # 1년 (252일)
    
    def calculate(self, returns: List[float]) -> Dict:
        """
        VaR 계산 (Modified VaR + Historical VaR)
        
        Returns:
            {
                'normal_var': float,      # 정규분포 VaR (기존)
                'modified_var': float,    # Modified VaR (Cornish-Fisher)
                'historical_var': float,  # Historical VaR (비모수)
                'skewness': float,        # 왜도
                'kurtosis': float,        # 첨도
                'tail_risk_adjusted': bool
            }
        """
        if len(returns) < self.window:
            return {
                'normal_var': 0.0,
                'modified_var': 0.0,
                'historical_var': 0.0,
                'skewness': 0.0,
                'kurtosis': 0.0,
                'tail_risk_adjusted': False,
                'warning': f'데이터 부족 (필요: {self.window}, 현재: {len(returns)})'
            }
        
        # 1. 기본 통계
        mu = np.mean(returns)
        sigma = np.std(returns)
        z_score = norm.ppf(1 - self.confidence)
        
        # 2. 정규분포 VaR (기존 방식)
        normal_var = -(mu + z_score * sigma)
        
        # 3. Modified VaR (Cornish-Fisher Expansion)
        skewness = self._calculate_skewness(returns)
        kurtosis = self._calculate_kurtosis(returns)
        
        # Cornish-Fisher Expansion: z_mod = z + (z^2 - 1)*skewness/6 + (z^3 - 3z)*(kurtosis - 3)/24
        z_mod = (
            z_score +
            (z_score**2 - 1) * skewness / 6 +
            (z_score**3 - 3 * z_score) * (kurtosis - 3) / 24
        )
        
        modified_var = -(mu + z_mod * sigma)
        
        # 4. Historical VaR (비모수)
        sorted_returns = np.sort(returns)
        var_index = int((1 - self.confidence) * len(sorted_returns))
        historical_var = -sorted_returns[var_index] if var_index < len(sorted_returns) else 0.0
        
        # 5. 팻테일 감지 (첨도 > 3 이면 팻테일)
        tail_risk_adjusted = kurtosis > 3.0
        
        return {
            'normal_var': normal_var,
            'modified_var': modified_var,
            'historical_var': historical_var,
            'skewness': skewness,
            'kurtosis': kurtosis,
            'tail_risk_adjusted': tail_risk_adjusted,
            'recommendation': self._get_recommendation(modified_var, normal_var, tail_risk_adjusted),
            'method': 'cornish_fisher' if tail_risk_adjusted else 'normal'
        }
    
    def _calculate_skewness(self, returns: List[float]) -> float:
        """왜도 (Skewness) 계산"""
        n = len(returns)
        mean = np.mean(returns)
        std = np.std(returns)
        if std == 0:
            return 0.0
        return np.mean(((returns - mean) / std) ** 3)
    
    def _calculate_kurtosis(self, returns: List[float]) -> float:
        """첨도 (Kurtosis) 계산 (초과첨도 아님)"""
        n = len(returns)
        mean = np.mean(returns)
        std = np.std(returns)
        if std == 0:
            return 3.0
        return np.mean(((returns - mean) / std) ** 4)
    
    def _get_recommendation(self, modified_var: float, normal_var: float, 
                           tail_risk_adjusted: bool) -> str:
        """권고사항 생성"""
        if not tail_risk_adjusted:
            return "정규분포 가정 적합 (정상 시장)"
        
        ratio = modified_var / normal_var if normal_var > 0 else 1.0
        if ratio > 1.3:
            return "⚠️ Modified VaR 사용 권장 (한국 시장 팻테일 반영)"
        elif ratio > 1.1:
            return "💡 Modified VaR 검토 (팻테일 가능성 존재)"
        else:
            return "✅ 정규분포 가정 유효"