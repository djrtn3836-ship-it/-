"""심층 분석 간격 제한 회귀 테스트 (2026-10-08 자원 폭발 사고).

사고: 틱마다 analyze() 호출 → decisions 2,854,924건/일, DB +1.5GB,
      로그 2.2GB, 메모리 7.3GB (178종목 × 6.5시간 × 매틱).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _app():
    """클래스 전체 초기화(설정/텔레그램/모델 로드) 없이 스로틀 상태만 구성."""
    from app.bootstrap import Bootstrapper
    app = Bootstrapper.__new__(Bootstrapper)
    app._analysis_interval_sec = 300.0
    app._signal_bypass_pct = 2.0
    app._analysis_last_at = {}
    return app


def test_first_call_always_allowed():
    app = _app()
    assert app._should_analyze("005930", 0.0, 1000.0) is True


def test_same_ticker_blocked_within_interval():
    app = _app()
    assert app._should_analyze("005930", 0.3, 1000.0) is True
    app._analysis_last_at["005930"] = 1000.0
    assert app._should_analyze("005930", 0.3, 1100.0) is False   # 100초 뒤
    assert app._should_analyze("005930", 0.3, 1301.0) is True    # 301초 뒤


def test_different_ticker_independent():
    app = _app()
    app._analysis_last_at["005930"] = 1000.0
    assert app._should_analyze("000660", 0.3, 1010.0) is True


def test_surge_bypasses_interval():
    app = _app()
    app._analysis_last_at["005930"] = 1000.0
    assert app._should_analyze("005930", 2.5, 1001.0) is True    # 급등락은 즉시
    assert app._should_analyze("005930", 2.0, 1001.0) is True    # 경계값 포함


def test_defaults_are_sane():
    app = _app()
    assert app._analysis_interval_sec >= 60
    assert app._signal_bypass_pct > 0
