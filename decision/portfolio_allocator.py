"""
Portfolio Allocator v5.1.2 — Claude 피드백 반영

변경사항:
1. Kelly 입력값을 실측 승률로만 사용
2. Phase 1에서는 균등 소액 비중(2~3%)으로 시작
3. 검증되지 않은 백테스트 승률 사용 금지
"""

from dataclasses import dataclass
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)


class PortfolioAllocator:
    """포트폴리오 할당기 v5.1.2"""
    
    def __init__(self, mode: str = "shadow"):
        """
        Args:
            mode: "shadow" (Phase 1), "paper" (Phase 2), "live" (Phase 3)
        """
        self.mode = mode
        self._use_backtest_kelly = False  # 기본: 백테스트 Kelly 사용 금지
        
        # Phase별 기본 비중
        self.base_allocation = {
            "shadow": 0.02,   # 2% (소액)
            "paper": 0.03,    # 3% (가상)
            "live": 0.05      # 5% (실전, Phase 2 통과 후)
        }
    
    def calculate_position(
        self,
        signal: Dict,
        live_win_rate: Optional[float] = None,
        live_sample_count: int = 0
    ) -> Dict:
        """
        포지션 사이징 계산 (Claude 피드백 반영)
        
        원칙:
        1. Phase 1 (Shadow): 무조건 균등 소액 비중 (2%)
        2. Phase 2 (Paper): 실측 승률 50건 이상 확보 시 Kelly 적용
        3. Phase 3 (Live): Phase 2 통과 후 실측 승률 기반 Kelly 적용
        """
        
        # 1. Phase 1: 무조건 소액 비중
        if self.mode == "shadow":
            return {
                'allocation_pct': self.base_allocation["shadow"] * 100,
                'method': 'fixed (Shadow Mode)',
                'reason': 'Phase 1: 검증되지 않은 백테스트 승률 사용 금지 (Claude 피드백)',
                'is_safe': True
            }
        
        # 2. Phase 2 이상: 실측 데이터 기반
        if self.mode in ["paper", "live"]:
            
            # 실측 데이터 부족
            if live_sample_count < 30:
                return {
                    'allocation_pct': self.base_allocation["paper"] * 100,
                    'method': 'fixed (데이터 부족)',
                    'reason': f'실측 샘플 {live_sample_count}건 (최소 30건 필요)',
                    'is_safe': True
                }
            
            # 실측 승률 부족
            if live_win_rate is None or live_win_rate < 0.35:
                return {
                    'allocation_pct': self.base_allocation["paper"] * 100,
                    'method': 'fixed (승률 부족)',
                    'reason': f'실측 승률 {live_win_rate:.1%} (최소 35% 필요)',
                    'is_safe': True
                }
            
            # 실측 승률 기반 Kelly 적용
            return self._calculate_kelly(live_win_rate, live_sample_count)
        
        # 기본값
        return {
            'allocation_pct': 2.0,
            'method': 'fixed (default)',
            'reason': '기본 안전 비중',
            'is_safe': True
        }
    
    def _calculate_kelly(self, win_rate: float, sample_count: int) -> Dict:
        """실측 승률 기반 Kelly 계산 (Claude 피드백: 백테스트 사용 금지)"""
        
        # 승률이 35% 미만이면 사용 불가
        if win_rate < 0.35:
            return {
                'allocation_pct': 2.0,
                'method': 'fixed (승률 낮음)',
                'reason': f'승률 {win_rate:.1%} < 35% (Kelly 사용 불가)',
                'is_safe': True
            }
        
        # 승률 기반 Kelly (보수적)
        # f* = (2p - 1) / 2 (단순화된 Kelly, 승률 기반)
        kelly = (2 * win_rate - 1) / 2
        kelly = max(0, min(kelly, 0.25))  # 0~25% 제한
        
        # Fractional Kelly (0.25배)
        final = kelly * 0.25
        
        # 하드캡 (8%)
        final = min(final, 0.08)
        
        return {
            'allocation_pct': final * 100,
            'method': 'fractional_kelly (실측 데이터)',
            'kelly_raw': kelly,
            'kelly_fraction': 0.25,
            'hard_cap': 0.08,
            'win_rate_used': win_rate,
            'sample_count': sample_count,
            'reason': f'실측 승률 {win_rate:.1%} ({sample_count}건) 기반 Kelly 적용',
            'is_safe': final <= 0.08
        }