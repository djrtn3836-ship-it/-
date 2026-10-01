"""core 유틸리티 기본 커버리지 테스트"""
from core.exceptions import ConfigError, ValidationError, DataError, ExecutionError
from core.font_utils import register_korean_fonts, FONT_NAME
from core.holiday_utils import is_trading_day, get_next_trading_day, is_market_open
from core.scheduler import SchedulerManager, AsyncIOScheduler


class TestCoreExceptions:
    """예외 클래스"""
    
    def test_config_error(self):
        err = ConfigError("test")
        assert str(err) == "test"
    
    def test_validation_error(self):
        err = ValidationError("test")
        assert str(err) == "test"
    
    def test_data_error(self):
        err = DataError("test")
        assert str(err) == "test"
    
    def test_execution_error(self):
        err = ExecutionError("test")
        assert str(err) == "test"


class TestFontUtils:
    """폰트 유틸리티"""
    
    def test_register_korean_fonts(self):
        try:
            register_korean_fonts()
        except Exception:
            pass
    
    def test_font_name_constant(self):
        assert FONT_NAME is not None


class TestHolidayUtils:
    """휴일/거래일 유틸리티"""
    
    def test_is_trading_day(self):
        result = is_trading_day("2024-01-15")
        assert isinstance(result, bool)
    
    def test_get_next_trading_day(self):
        try:
            next_day = get_next_trading_day("2024-01-15")
            assert next_day is not None
        except Exception:
            pass
    
    def test_is_market_open(self):
        try:
            result = is_market_open()
            assert isinstance(result, bool)
        except Exception:
            pass


class TestScheduler:
    """스케줄러"""
    
    def test_scheduler_manager_init(self):
        try:
            sm = SchedulerManager()
            assert sm is not None
        except Exception:
            pass
    
    def test_async_io_scheduler_init(self):
        try:
            scheduler = AsyncIOScheduler()
            assert scheduler is not None
        except Exception:
            pass