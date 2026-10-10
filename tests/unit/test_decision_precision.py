"""판단력 정밀화 2건 회귀 테스트 (2026-10-10).

① 근거 기반 판단 보류: 이력/ATR이 없으면 신호를 내지 않는다.
   (이전: 지표가 비어 있어도 전략을 돌려 무근거 신호가 나갈 수 있었다)
② 알림 반복 억제: 같은 종목·행동은 쿨다운(기본 30분) 내 재발송하지 않는다.
   (이전: 5분 스로틀 하에서도 같은 종목이 계속 신호를 내면 알림이 반복됐다)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestSufficientEvidence:

    def _fn(self):
        from scanner.deep_analyzer import DeepAnalyzer
        return DeepAnalyzer._has_sufficient_evidence

    def test_requires_both_history_and_atr(self):
        fn = self._fn()
        assert fn({"rsi": 55, "momentum": 0.1}, 120.0) is True
        assert fn({}, 120.0) is False           # 이력 없음
        assert fn(None, 120.0) is False
        assert fn({"rsi": 55}, 0.0) is False    # ATR 없음
        assert fn({"rsi": 55}, -1.0) is False   # 비정상 ATR

    def test_bad_types_do_not_raise(self):
        fn = self._fn()
        assert fn({"rsi": 55}, "x") is False
        assert fn({"rsi": 55}, None) is False


class TestAlertCooldown:

    def _app(self, cooldown=1800.0):
        from app.bootstrap import Bootstrapper
        app = Bootstrapper.__new__(Bootstrapper)
        app._alert_last_at = {}
        app._alert_cooldown_sec = cooldown
        return app

    def test_first_alert_sent(self):
        app = self._app()
        assert app._should_send_alert("005930", "SIGNAL_ENTRY", now=1000.0) is True

    def test_repeat_suppressed_within_cooldown(self):
        app = self._app()
        assert app._should_send_alert("005930", "SIGNAL_ENTRY", now=1000.0) is True
        assert app._should_send_alert("005930", "SIGNAL_ENTRY", now=1100.0) is False
        assert app._should_send_alert("005930", "SIGNAL_ENTRY", now=2799.0) is False
        assert app._should_send_alert("005930", "SIGNAL_ENTRY", now=2801.0) is True

    def test_other_ticker_and_action_independent(self):
        app = self._app()
        assert app._should_send_alert("005930", "SIGNAL_ENTRY", now=1000.0) is True
        assert app._should_send_alert("000660", "SIGNAL_ENTRY", now=1001.0) is True
        assert app._should_send_alert("005930", "SELL", now=1002.0) is True
        assert app._should_send_alert("005930", "SIGNAL_ENTRY", now=1003.0) is False

    def test_zero_cooldown_always_sends(self):
        app = self._app(cooldown=0.0)
        assert app._should_send_alert("005930", "BUY", now=1000.0) is True
        assert app._should_send_alert("005930", "BUY", now=1000.0) is True
