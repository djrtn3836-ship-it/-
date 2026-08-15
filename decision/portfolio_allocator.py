"""
Portfolio Allocator v7.0.0 — Claude 피드백 + Kelly 고도화
변경사항:
1. 기존 Shadow/Paper/Live 안전장치 완전 유지
2. 승률 기반 Kelly 계산 로직 고도화 (실측 데이터 반영)
3. Half-Kelly + 하드캡(8%) 보존
"""

from dataclasses import dataclass
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)


class PortfolioAllocator:
    """포트폴리오 할당기 v7.0.0"""
    
    def __init__(self, mode: str = "shadow"):
        """
        Args:
            mode: "shadow" (Phase 1), "paper" (Phase 2), "live" (Phase 3)
        """
        self.mode = mode
        
        # Phase별 기본 비중 (안전장치)
        self.base_allocation = {
            "shadow": 0.02,   # 2% (소액)
            "paper": 0.03,    # 3% (가상)
            "live": 0.05      # 5% (실전, Phase 2 통과 후)
        }
        # 최대 허용 비중 (안전장치)
        self.max_allocation = {
            "shadow": 0.02,
            "paper": 0.08,
            "live": 0.15
        }
    
    def calculate_position(
        self,
        signal: Dict,
        live_win_rate: Optional[float] = None,
        live_sample_count: int = 0,
        avg_win: Optional[float] = None,
        avg_loss: Optional[float] = None
    ) -> Dict:
        """
        포지션 사이징 계산 (Claude 피드백 + Kelly 고도화)
        
        원칙 (완전 유지):
        1. Phase 1 (Shadow): 무조건 균등 소액 비중 (2%) → 🔥 이게 제일 중요
        2. Phase 2 (Paper): 실측 승률 50건 이상 확보 시 Kelly 적용
        3. Phase 3 (Live): Phase 2 통과 후 실측 승률 기반 Kelly 적용
        """
        
        # 🔥 1. Phase 1: 무조건 소액 비중 (퇴보 없이 유지)
        if self.mode == "shadow":
            return {
                'allocation_pct': self.base_allocation["shadow"] * 100,
                'method': 'fixed (Shadow Mode)',
                'reason': 'Phase 1: 검증되지 않은 백테스트 승률 사용 금지 (Claude 피드백)',
                'is_safe': True
            }
        
        # 2. Phase 2 이상: 실측 데이터 기반
        if self.mode in ["paper", "live"]:
            
            # 실측 데이터 부족 (안전장치)
            if live_sample_count < 30:
                return {
                    'allocation_pct': self.base_allocation["paper"] * 100,
                    'method': 'fixed (데이터 부족)',
                    'reason': f'실측 샘플 {live_sample_count}건 (최소 30건 필요)',
                    'is_safe': True
                }
            
            # 실측 승률 부족 (안전장치)
            if live_win_rate is None or live_win_rate < 0.35:
                return {
                    'allocation_pct': self.base_allocation["paper"] * 100,
                    'method': 'fixed (승률 부족)',
                    'reason': f'실측 승률 {live_win_rate:.1%} (최소 35% 필요)',
                    'is_safe': True
                }
            
            # 🔥 고도화: 실측 승률 + 평균 손익 기반 Kelly 적용
            return self._calculate_advanced_kelly(
                live_win_rate, 
                live_sample_count,
                avg_win or 3.0,
                avg_loss or 2.0
            )
        
        # 기본값 (안전)
        return {
            'allocation_pct': 2.0,
            'method': 'fixed (default)',
            'reason': '기본 안전 비중',
            'is_safe': True
        }
    
    def _calculate_advanced_kelly(self, win_rate: float, sample_count: int, avg_win: float, avg_loss: float) -> Dict:
        """
        실측 승률 + 평균 손익 기반 Kelly 계산 (고도화)
        - Half-Kelly 적용 (보수적)
        - 하드캡 적용 (Phase별 max_allocation)
        """
        # Kelly 공식: f* = (p * b - q) / b (b = avg_win / avg_loss)
        if avg_loss == 0:
            avg_loss = 1.0  # ZeroDivision 방지
        
        b = avg_win / avg_loss
        q = 1 - win_rate
        kelly_raw = (win_rate * b - q) / b if b > 0 else 0
        kelly_raw = max(0, kelly_raw)  # 음수 방지 (도박 금지)
        
        # Half-Kelly (Fractional 0.25배) + 하드캡
        fraction = 0.25
        kelly_final = kelly_raw * fraction
        max_cap = self.max_allocation.get(self.mode, 0.08)
        kelly_final = min(kelly_final, max_cap)
        kelly_final = max(0.01, kelly_final)  # 최소 1%
        
        return {
            'allocation_pct': kelly_final * 100,
            'method': 'fractional_kelly (실측 데이터 기반 고도화)',
            'kelly_raw': kelly_raw,
            'kelly_fraction': fraction,
            'hard_cap': max_cap,
            'win_rate_used': win_rate,
            'sample_count': sample_count,
            'avg_win_used': avg_win,
            'avg_loss_used': avg_loss,
            'reason': f'실측 승률 {win_rate:.1%} ({sample_count}건) + 평균 손익({avg_win:.1f}/{avg_loss:.1f}) 기반 Kelly 적용',
            'is_safe': kelly_final <= 0.08
        }