"""
validation/var_calculator.py - v7.4.0 FINAL (Modified VaR + 리스크 조정 팩터)
- 기존 Modified VaR (Cornish-Fisher) 유지
- 🔥 신규: calculate() 결과에 risk_adjustment_factor (0.5~1.0) 추가
  (VaR가 높을수록 팩터 감소 → 포지션 비중 축소)
- 경로: validation/var_calculator.py (portfolio_allocator.py에서 import)
"""

import numpy as np
from scipy.stats import norm
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class VaRCalculator:
    def __init__(self, confidence: float = 0.95, window: int = 252):
        self.confidence = confidence
        self.window = window

    def calculate(self, returns: List[float]) -> Dict:
        if len(returns) < self.window:
            return {
                'normal_var': 0.0,
                'modified_var': 0.0,
                'historical_var': 0.0,
                'skewness': 0.0,
                'kurtosis': 0.0,
                'tail_risk_adjusted': False,
                'risk_adjustment_factor': 1.0,
                'warning': f'데이터 부족 (필요: {self.window}, 현재: {len(returns)})'
            }

        mu = np.mean(returns)
        sigma = np.std(returns)
        z_score = norm.ppf(1 - self.confidence)

        # 정규 VaR
        normal_var = -(mu + z_score * sigma)

        # Modified VaR (Cornish-Fisher)
        skewness = self._calculate_skewness(returns)
        kurtosis = self._calculate_kurtosis(returns)

        z_mod = (z_score +
                 (z_score**2 - 1) * skewness / 6 +
                 (z_score**3 - 3 * z_score) * (kurtosis - 3) / 24)
        modified_var = -(mu + z_mod * sigma)

        # Historical VaR
        sorted_returns = np.sort(returns)
        var_index = int((1 - self.confidence) * len(sorted_returns))
        historical_var = -sorted_returns[var_index] if var_index < len(sorted_returns) else 0.0

        tail_risk_adjusted = kurtosis > 3.0

        # ============================================================
        # 🔥 v7.4.0: VaR 기반 리스크 조정 팩터 (0.5 ~ 1.0)
        # - modified_var가 5% 이상이면 팩터 0.5로 하락
        # - modified_var가 2% 미만이면 팩터 1.0 (안전)
        # ============================================================
        var_pct = modified_var * 100
        if var_pct >= 5.0:
            risk_adj = 0.5
        elif var_pct >= 3.0:
            risk_adj = 0.75
        elif var_pct >= 1.5:
            risk_adj = 0.9
        else:
            risk_adj = 1.0

        return {
            'normal_var': normal_var,
            'modified_var': modified_var,
            'historical_var': historical_var,
            'skewness': skewness,
            'kurtosis': kurtosis,
            'tail_risk_adjusted': tail_risk_adjusted,
            'risk_adjustment_factor': risk_adj,
            'recommendation': self._get_recommendation(modified_var, normal_var, tail_risk_adjusted),
            'method': 'cornish_fisher' if tail_risk_adjusted else 'normal'
        }

    def _calculate_skewness(self, returns: List[float]) -> float:
        n = len(returns)
        mean = np.mean(returns)
        std = np.std(returns)
        if std == 0:
            return 0.0
        return np.mean(((returns - mean) / std) ** 3)

    def _calculate_kurtosis(self, returns: List[float]) -> float:
        n = len(returns)
        mean = np.mean(returns)
        std = np.std(returns)
        if std == 0:
            return 3.0
        return np.mean(((returns - mean) / std) ** 4)

    def _get_recommendation(self, modified_var: float, normal_var: float, tail_risk_adjusted: bool) -> str:
        if not tail_risk_adjusted:
            return "정규분포 가정 적합 (정상 시장)"
        ratio = modified_var / normal_var if normal_var > 0 else 1.0
        if ratio > 1.3:
            return "⚠️ Modified VaR 사용 권장 (한국 시장 팻테일 반영)"
        elif ratio > 1.1:
            return "💡 Modified VaR 검토 (팻테일 가능성 존재)"
        else:
            return "✅ 정규분포 가정 유효"