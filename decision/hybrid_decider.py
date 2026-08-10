"""
Hybrid Decider v5.1.2
통합 판단 (7개 엔진 Consensus)
"""

from typing import Dict, List

from core.logger import setup_logger

logger = setup_logger("decider")


class HybridDecider:
    """통합 판단기"""
    
    def __init__(self):
        self.engines = [
            "macro", "sector", "stock", "theme",
            "korean", "regime", "historical"
        ]
    
    def decide(self, data: Dict) -> Dict:
        """최종 판단 생성"""
        score = data.get("score", 0.5)
        
        # 판단 매핑
        if score >= 0.75:
            action = "강력 매수"
        elif score >= 0.60:
            action = "매수"
        elif score >= 0.45:
            action = "중립 (관망)"
        elif score >= 0.30:
            action = "매도"
        elif score >= 0.15:
            action = "부분 매도"
        else:
            action = "전량 매도"
        
        return {
            "action": action,
            "score": score,
            "confidence": min(0.95, 0.5 + score * 0.5),
            "reasoning": self._generate_reasoning(data)
        }
    
    def _generate_reasoning(self, data: Dict) -> List[str]:
        """근거 생성"""
        reasons = []
        details = data.get("details", {})
        
        if details.get("macro", 0) > 0.6:
            reasons.append("매크로 환경 양호")
        if details.get("sector", 0) > 0.6:
            reasons.append("업종 강세")
        if details.get("stock", 0) > 0.6:
            reasons.append("종목 펀더멘탈 양호")
        
        return reasons if reasons else ["특이사항 없음"]