"""
core/holiday_utils.py - v2.0 (한국 영업일 판단 + 캐싱 + 타입 안전성)
- 주말 하드코딩 차단
- pytimekr 기반 공휴일 자동 인식 (년도별 캐싱)
- datetime, date 객체 모두 처리 가능
- 프로그램 시작 시 비거래일 조기 종료 지원 (외부에서 활용)
"""

from datetime import datetime, date, timedelta
from typing import Union, Optional
from core.logger import setup_logger

logger = setup_logger("holiday_utils")

# ============================================================
# 1. pytimekr 로드 (선택적)
# ============================================================
try:
    import pytimekr
    HAS_PYTIMEKR = True
    logger.info("✅ pytimekr 로드 완료 (한국 공휴일 자동 인식)")
except ImportError:
    HAS_PYTIMEKR = False
    logger.warning("⚠️ pytimekr 미설치 → 평일(월~금) 기준으로만 동작")

# ============================================================
# 2. 공휴일 캐시 (년도별)
# ============================================================
_holiday_cache = {}  # {year: set(date_objects)}

def _get_holidays(year: int) -> set:
    """해당 연도의 공휴일 목록을 캐시하여 반환"""
    if year in _holiday_cache:
        return _holiday_cache[year]

    if not HAS_PYTIMEKR:
        _holiday_cache[year] = set()
        return _holiday_cache[year]

    try:
        holidays = pytimekr.holidays(year)
        # holidays는 list of datetime.date
        _holiday_cache[year] = set(holidays)
    except Exception as e:
        logger.warning(f"⚠️ {year}년 공휴일 조회 실패: {e}")
        _holiday_cache[year] = set()

    return _holiday_cache[year]

# ============================================================
# 3. 거래일 판단 (핵심 함수)
# ============================================================
def is_trading_day(dt: Optional[Union[datetime, date]] = None) -> bool:
    """
    한국 증시 거래일 여부 반환 (주말 + 공휴일 제외)

    Args:
        dt: datetime 또는 date 객체 (기본값: 현재 시간)

    Returns:
        True: 거래일, False: 휴일
    """
    if dt is None:
        dt = datetime.now()

    # 날짜 객체로 통일 (datetime -> date)
    if isinstance(dt, datetime):
        target_date = dt.date()
    else:
        target_date = dt  # 이미 date

    # 1. 주말 체크 (토=5, 일=6)
    if target_date.weekday() >= 5:
        return False

    # 2. 공휴일 체크 (캐시 사용)
    holidays = _get_holidays(target_date.year)
    if target_date in holidays:
        return False

    # 3. 기본: 평일이면 True
    return True

# ============================================================
# 4. 다음 거래일 계산
# ============================================================
def get_next_trading_day(dt: Optional[Union[datetime, date]] = None) -> datetime:
    """지정된 날짜 이후의 첫 번째 거래일 반환 (datetime 객체)"""
    if dt is None:
        dt = datetime.now()
    if isinstance(dt, datetime):
        current = dt + timedelta(days=1)
    else:
        current = datetime.combine(dt, datetime.min.time()) + timedelta(days=1)

    while not is_trading_day(current):
        current += timedelta(days=1)
    return current

# ============================================================
# 5. 현재 장중 여부 (추가 편의)
# ============================================================
def is_market_open() -> bool:
    """현재 시간이 장중(09:00~15:30)인지 확인 (거래일만)"""
    now = datetime.now()
    if not is_trading_day(now):
        return False
    return 9 <= now.hour <= 15 and (now.hour < 15 or now.minute < 30)