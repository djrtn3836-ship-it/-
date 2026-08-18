"""
filters/macro_filter.py - v5.1.3 (거시 데이터 통합)
- 🔥 수정: 하드코딩된 기본값 대신 macro_collector의 실제 데이터를 사용
- 수집 실패 시 기본값 사용 (Fallback)
"""

from typing import Dict

from core.logger import setup_logger
from scheduler.macro_collector import get_cached_macro  # 🔥 추가

logger = setup_logger("macro")


class MacroFilter:
    """매크로 필터 (4개 지표) - 거시 데이터 연동"""

    def check(self, data: Dict) -> Dict:
        """매크로 점수 산출 (실제 거시 데이터 사용)"""
        # 🔥 거시 데이터 가져오기
        macro = get_cached_macro()

        # data에서 우선, 없으면 macro에서 가져오기 (Fallback)
        kospi_trend = data.get("kospi_trend", macro.kospi_trend)
        usdkrw = data.get("usdkrw", macro.usdkrw)
        bond_3y = data.get("bond_3y", macro.bond_3y)
        foreigner_futures = data.get("foreigner_futures", macro.foreigner_futures)

        score = 0.0
        indicators = {}

        # 1. KOSPI 200 추세 (5일)
        indicators["kospi_trend"] = kospi_trend
        if kospi_trend > 1:
            score += 0.3
        elif kospi_trend > 0:
            score += 0.15

        # 2. 환율 (USD/KRW) - 1350 미만이면 우호
        indicators["usdkrw"] = usdkrw
        if usdkrw < 1350:
            score += 0.3
        elif usdkrw < 1400:
            score += 0.15

        # 3. 외국인 수급 (선물)
        indicators["foreigner_futures"] = foreigner_futures
        if foreigner_futures > 0:
            score += 0.2

        # 4. 금리 (3년물) - 4% 미만이면 우호
        indicators["bond_3y"] = bond_3y
        if bond_3y < 4.0:
            score += 0.2
        elif bond_3y < 4.5:
            score += 0.1

        # 로그: 실제 사용된 값 출력 (디버깅)
        logger.debug(f"📊 매크로 점수: {score:.2f} (KOSPI: {kospi_trend:.2f}, 환율: {usdkrw:.0f}, 금리: {bond_3y:.2f})")

        return {
            "score": min(1.0, score),
            "indicators": indicators
        }