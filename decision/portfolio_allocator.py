"""
Portfolio Allocator v7.6.0 — Claude 피드백 + Kelly 고도화 + 포트폴리오 VaR 연동
변경사항 (v7.4.0 → v7.6.0):
1. 기존 Shadow/Paper/Live 안전장치 완전 유지
2. calculate_position()에 global_risk_penalty 인자 추가 (PortfolioManager에서 전달)
3. 최종 비중 = Kelly 비중 × global_risk_penalty (포트폴리오 레벨 리스크 조정)
4. Half-Kelly + 하드캡(8%) + VaR 패널티 삼중 안전장치
"""

from dataclasses import dataclass
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)


class PortfolioAllocator:
    """포트폴리오 할당기 v7.6.0 (포트폴리오 VaR 연동)"""

    def __init__(self, mode: str = "shadow"):
        self.mode = mode
        self.base_allocation = {"shadow": 0.02, "paper": 0.03, "live": 0.05}
        self.max_allocation = {"shadow": 0.02, "paper": 0.08, "live": 0.15}

    def calculate_position(
        self,
        signal: Dict,
        live_win_rate: Optional[float] = None,
        live_sample_count: int = 0,
        avg_win: Optional[float] = None,
        avg_loss: Optional[float] = None,
        var_95: Optional[float] = None,
        global_risk_penalty: float = 1.0  # 🔥 v7.6.0: 포트폴리오 VaR 패널티
    ) -> Dict:

        # 🔥 Phase 1: 무조건 소액 비중 (안전장치 1)
        if self.mode == "shadow":
            return {
                'allocation_pct': self.base_allocation["shadow"] * 100,
                'method': 'fixed (Shadow Mode)',
                'reason': 'Phase 1: 검증되지 않은 백테스트 승률 사용 금지',
                'is_safe': True,
                'global_penalty_applied': 1.0,
            }

        # Phase 2 이상: 실측 데이터 기반
        if self.mode in ["paper", "live"]:
            if live_sample_count < 30:
                return {
                    'allocation_pct': self.base_allocation["paper"] * 100,
                    'method': 'fixed (데이터 부족)',
                    'reason': f'실측 샘플 {live_sample_count}건 (최소 30건 필요)',
                    'is_safe': True,
                    'global_penalty_applied': 1.0,
                }
            if live_win_rate is None or live_win_rate < 0.35:
                return {
                    'allocation_pct': self.base_allocation["paper"] * 100,
                    'method': 'fixed (승률 부족)',
                    'reason': f'실측 승률 {live_win_rate:.1%} (최소 35% 필요)',
                    'is_safe': True,
                    'global_penalty_applied': 1.0,
                }

            return self._calculate_advanced_kelly(
                live_win_rate,
                live_sample_count,
                avg_win or 3.0,
                avg_loss or 2.0,
                var_95,
                global_risk_penalty  # 🔥 v7.6.0 전달
            )

        return {
            'allocation_pct': 2.0,
            'method': 'fixed (default)',
            'reason': '기본 안전 비중',
            'is_safe': True,
            'global_penalty_applied': 1.0,
        }

    def _calculate_advanced_kelly(
        self,
        win_rate: float,
        sample_count: int,
        avg_win: float,
        avg_loss: float,
        var_95: Optional[float] = None,
        global_risk_penalty: float = 1.0
    ) -> Dict:
        # 1. 기본 Kelly
        if avg_loss == 0:
            avg_loss = 1.0
        b = avg_win / avg_loss
        q = 1 - win_rate
        kelly_raw = (win_rate * b - q) / b if b > 0 else 0
        kelly_raw = max(0, kelly_raw)

        # 2. Fractional (Half-Kelly, 0.25배) + 하드캡
        fraction = 0.25
        kelly_final = kelly_raw * fraction
        max_cap = self.max_allocation.get(self.mode, 0.08)

        # 3. 개별 종목 VaR 패널티 (기존 v7.4.0 로직)
        var_penalty = 1.0
        if var_95 is not None and var_95 > 0:
            var_pct = var_95 * 100
            if var_pct >= 5.0:
                var_penalty = 0.50
            elif var_pct >= 3.0:
                var_penalty = 0.75
            elif var_pct >= 1.5:
                var_penalty = 0.90
            if var_penalty < 1.0:
                logger.info(f"📉 개별 VaR {var_pct:.1f}% → 패널티 {var_penalty:.0%}")

        # 🔥 4. 글로벌 포트폴리오 VaR 패널티 (v7.6.0 신규)
        global_penalty = min(1.0, max(0.5, global_risk_penalty))
        if global_penalty < 1.0:
            logger.info(f"📊 포트폴리오 VaR 패널티 적용: {global_penalty:.0%}")

        # 최종 비중 = Kelly × 개별패널티 × 글로벌패널티
        kelly_final = kelly_final * var_penalty * global_penalty
        kelly_final = min(kelly_final, max_cap)
        kelly_final = max(0.01, kelly_final)

        return {
            'allocation_pct': kelly_final * 100,
            'method': 'fractional_kelly + VaR(개별+포트폴리오) (v7.6.0)',
            'kelly_raw': kelly_raw,
            'kelly_fraction': fraction,
            'hard_cap': max_cap,
            'var_penalty': var_penalty,
            'global_penalty': global_penalty,
            'var_95_used': var_95,
            'win_rate_used': win_rate,
            'sample_count': sample_count,
            'avg_win_used': avg_win,
            'avg_loss_used': avg_loss,
            'reason': f'승률 {win_rate:.1%} + VaR {var_95*100:.1f}% + 글로벌패널티 {global_penalty:.0%}',
            'is_safe': kelly_final <= 0.08
        }