"""
core/holiday_utils.py - 한국 영업일(거래일) 판단 유틸리티
- pytimekr 기반으로 공휴일 자동 인식
- 라이브러리 없을 경우 평일(월~금) 기준으로 폴백
"""
from datetime import datetime, timedelta
from core.logger import setup_logger

logger = setup_logger("holiday_utils")

try:
    import pytimekr
    HAS_PYTIMEKR = True
    logger.info("✅ pytimekr 로드 완료 (한국 공휴일 자동 인식)")
except ImportError:
    HAS_PYTIMEKR = False
    logger.warning("⚠️ pytimekr 미설치 → 평일(월~금) 기준으로만 동작 (pip install pytimekr)")


def is_trading_day(dt: datetime = None) -> bool:
    """
    해당 날짜가 한국 증시 거래일인지 반환 (공휴일/주말 제외)
    - dt: 확인할 날짜 (기본값: 현재 시간)
    - return: True(거래일) / False(휴일)
    """
    if dt is None:
        dt = datetime.now()
    
    # 1. 주말 체크 (토/일)
    if dt.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    
    # 2. 공휴일 체크 (pytimekr)
    if HAS_PYTIMEKR:
        try:
            holidays = pytimekr.holidays(dt.year)
            # date 객체로 변환하여 비교
            if dt.date() in holidays:
                return False
        except Exception as e:
            logger.warning(f"⚠️ 공휴일 조회 오류: {e} → 평일 기준으로 폴백")
            # 오류 시 평일 기준으로만 판단 (안전장치)
            return dt.weekday() < 5
    
    # 3. 기본: 평일이면 True
    return dt.weekday() < 5


def get_next_trading_day(dt: datetime = None) -> datetime:
    """다음 거래일 반환 (오늘 이후 첫 거래일)"""
    if dt is None:
        dt = datetime.now()
    dt = dt + timedelta(days=1)
    while not is_trading_day(dt):
        dt = dt + timedelta(days=1)
    return dt