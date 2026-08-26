"""
core/holiday_utils.py - v3.1 (holidays 패키지 호환성 강화)
"""

from datetime import date, datetime, timedelta

from core.logger import setup_logger

logger = setup_logger("holiday_utils")

# ============================================================
# 1. holidays 패키지 로드 (여러 방식 시도)
# ============================================================
_holidays_instance = None

try:
    import holidays

    HAS_HOLIDAYS = True
    # 방법 A: country_holidays
    try:
        _holidays_instance = holidays.country_holidays("KR")
        # 테스트: 2026년에 공휴일이 있는지 확인
        test_2026 = [d for d in _holidays_instance.keys() if d.year == 2026]
        if not test_2026:
            # 방법 B: KR 클래스 직접 사용
            _holidays_instance = holidays.KR()
            test_2026 = [d for d in _holidays_instance.keys() if d.year == 2026]
            if not test_2026:
                # 방법 C: years 매개변수 지정
                _holidays_instance = holidays.country_holidays("KR", years=2026)
        logger.info("✅ holidays 패키지 로드 완료 (한국 공휴일 자동 인식)")
    except Exception as e:
        logger.warning(f"⚠️ holidays 초기화 실패: {e}")
        _holidays_instance = None
        HAS_HOLIDAYS = False
except ImportError:
    HAS_HOLIDAYS = False
    logger.warning("⚠️ holidays 패키지 미설치 (pip install holidays)")

# Fallback: constants.py
try:
    from core.constants import HOLIDAYS
except ImportError:
    HOLIDAYS = []

# ============================================================
# 2. 공휴일 캐시
# ============================================================
_holiday_cache = {}


def _get_holidays(year: int) -> set:
    if year in _holiday_cache:
        return _holiday_cache[year]

    holidays_set = set()

    # 1) holidays 패키지 사용
    if HAS_HOLIDAYS and _holidays_instance is not None:
        try:
            for dt, name in _holidays_instance.items():
                if dt.year == year:
                    holidays_set.add(dt)
        except Exception as e:
            logger.debug(f"holidays 조회 실패: {e}")

    # 2) Fallback: constants.py (holidays가 없거나 실패할 때)
    if not holidays_set:
        for h_str in HOLIDAYS:
            try:
                holidays_set.add(date.fromisoformat(h_str))
            except ValueError:
                pass

    _holiday_cache[year] = holidays_set
    return holidays_set


# ============================================================
# 3. 거래일 판단
# ============================================================
def is_trading_day(dt: datetime | date | None = None) -> bool:
    if dt is None:
        dt = datetime.now()
    if isinstance(dt, datetime):
        target_date = dt.date()
    else:
        target_date = dt

    if target_date.weekday() >= 5:
        return False

    holidays_set = _get_holidays(target_date.year)
    if target_date in holidays_set:
        return False

    return True


# ============================================================
# 4. 기타 함수
# ============================================================
def get_next_trading_day(dt: datetime | date | None = None) -> datetime:
    if dt is None:
        dt = datetime.now()
    if isinstance(dt, datetime):
        current = dt + timedelta(days=1)
    else:
        current = datetime.combine(dt, datetime.min.time()) + timedelta(days=1)
    while not is_trading_day(current):
        current += timedelta(days=1)
    return current


def is_market_open() -> bool:
    now = datetime.now()
    if not is_trading_day(now):
        return False
    return 9 <= now.hour <= 15 and (now.hour < 15 or now.minute < 30)
