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
            # 방법 B: KR 클래스 직접 사용            # _holidays_instance = holidays.KR()  # holidays 최신버전 호환성
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
_holiday_cache: dict[int, set[date]] = {}


def _get_holidays(year: int) -> set[date]:
    """해당 연도의 공휴일 집합(holidays 패키지 + constants 합집합).

    🔴 2026-10-10 수정(실버그): `country_holidays("KR")` 무인자 인스턴스는
    지연 확장이라 가져온 시점에 2026년만 확장돼 있었고, `keys()` 반복은
    확장된 연도만 돌려준다. 그 결과 **2027년 이후 공휴일이 통째로 누락**되어
    신정(1/1)·삼일절(3/1)을 거래일로 오판했다(2027-01-01 → True).
    → 연도별 인스턴스(`years=year`)로 조회하고, 명시 목록과 **합집합**으로
    보강한다(선거일·임시공휴일 등 패키지 누락 대비).
    """
    if year in _holiday_cache:
        return _holiday_cache[year]

    holidays_set: set[date] = set()

    # 1) holidays 패키지: 연도별 온디맨드(확장 문제 없음)
    if HAS_HOLIDAYS:
        try:
            import holidays as _holidays_pkg

            for dt in _holidays_pkg.country_holidays("KR", years=year).keys():
                if dt.year == year:
                    holidays_set.add(dt)
        except Exception as e:
            logger.debug(f"holidays 조회 실패({year}): {e}")

    # 2) 명시 목록(constants) 합집합 — 패키지가 놓친 날 보강
    for h_str in HOLIDAYS:
        try:
            d = date.fromisoformat(str(h_str).strip())
            if d.year == year:
                holidays_set.add(d)
        except ValueError:
            pass

    _holiday_cache[year] = holidays_set
    return holidays_set


# ============================================================
# 3. 거래일 판단
# ============================================================
def is_trading_day(dt: datetime | date | str | None = None) -> bool:
    if dt is None:
        dt = datetime.now()
    if isinstance(dt, str):
        try:
            dt = date.fromisoformat(dt.strip())
        except ValueError:
            return False
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
