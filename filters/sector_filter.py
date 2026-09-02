# filters/sector_filter.py - v5.1.4 (mypy strict 적용 - Session 24)
from typing import Any, Dict

from core.logger import setup_logger

logger = setup_logger("sector")


class SectorFilter:
    """섹터 필터 (3개 지표)"""

    def check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        score: float = 0.0
        indicators: Dict[str, Any] = {}

        rel_strength: float = float(data.get("sector_relative", 1.0))
        indicators["relative"] = rel_strength
        if rel_strength > 1.05:
            score += 0.4

        money_flow: float = float(data.get("sector_money_flow", 0))
        indicators["money_flow"] = money_flow
        if money_flow > 0:
            score += 0.3

        sector_rank: float = float(data.get("sector_rank", 50))
        indicators["rank"] = sector_rank
        if sector_rank < 20:
            score += 0.3

        return {"score": min(1.0, score), "indicators": indicators}
