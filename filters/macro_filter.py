"""
Macro Filter v5.1.2
매크로 환경 평가 (금리, 환율, VIX, VKOSPI)
"""

from typing import Dict

from core.logger import setup_logger

logger = setup_logger("macro")


class MacroFilter:
    """매크로 필터 (4개 지표)"""
    
    def check(self, data: Dict) -> Dict:
        """매크로 점수 산출"""
        score = 0.0
        indicators = {}
        
        # 1. KOSPI 200 추세 (5일)
        kospi_trend = data.get("kospi_trend", 0)
        indicators["kospi_trend"] = kospi_trend
        if kospi_trend > 1:
            score += 0.3
        
        # 2. 환율 (USD/KRW)
        usdkrw = data.get("usdkrw", 1300)
        indicators["usdkrw"] = usdkrw
        if usdkrw < 1350:
            score += 0.3
        
        # 3. 외국인 수급
        foreigner = data.get("foreigner_futures", 0)
        indicators["foreigner"] = foreigner
        if foreigner > 0:
            score += 0.2
        
        # 4. 금리
        bond_rate = data.get("bond_3y", 3.5)
        indicators["bond_rate"] = bond_rate
        if bond_rate < 4.0:
            score += 0.2
        
        return {
            "score": min(1.0, score),
            "indicators": indicators
        }