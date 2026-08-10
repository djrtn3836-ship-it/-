"""
Sector Filter v5.1.2
업종/섹터 평가 (상대강도, 자금흐름, 업종순위)
"""

from typing import Dict

from core.logger import setup_logger

logger = setup_logger("sector")


class SectorFilter:
    """섹터 필터 (3개 지표)"""
    
    def check(self, data: Dict) -> Dict:
        """섹터 점수 산출"""
        score = 0.0
        indicators = {}
        
        # 1. 업종 대비 상대강도
        rel_strength = data.get("sector_relative", 1.0)
        indicators["relative"] = rel_strength
        if rel_strength > 1.05:
            score += 0.4
        
        # 2. 자금 흐름
        money_flow = data.get("sector_money_flow", 0)
        indicators["money_flow"] = money_flow
        if money_flow > 0:
            score += 0.3
        
        # 3. 업종 순위 (상위 20%)
        sector_rank = data.get("sector_rank", 50)
        indicators["rank"] = sector_rank
        if sector_rank < 20:
            score += 0.3
        
        return {
            "score": min(1.0, score),
            "indicators": indicators
        }