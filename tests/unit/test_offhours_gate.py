"""장외 소음 차단 테스트 (2026-10-14 Session 113).

사고: 시간외단일가(~18:00) 틱이 워커에서 계속 분석돼 16:32~18:31에
포지션 재평가 이벤트가 53건+ 쌓였다(알림은 차단됐으나 기록·모의매매 오염).
→ 정규장(거래일 09:00~15:35) 외 틱은 워커에서 원천 드롭.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.bootstrap import Bootstrapper  # noqa: E402


class TestInSignalWindow:

    def _at(self, day, hhmm):
        h, m = map(int, hhmm.split(":"))
        return datetime(2026, 10, day, h, m)

    def test_regular_session_allowed(self):
        assert Bootstrapper._in_signal_window(self._at(14, "09:00")) is True   # 수요일
        assert Bootstrapper._in_signal_window(self._at(14, "12:00")) is True
        assert Bootstrapper._in_signal_window(self._at(14, "15:29")) is True

    def test_closing_grace_allowed(self):
        assert Bootstrapper._in_signal_window(self._at(14, "15:35")) is True

    def test_after_hours_blocked(self):
        assert Bootstrapper._in_signal_window(self._at(14, "15:36")) is False
        assert Bootstrapper._in_signal_window(self._at(14, "16:32")) is False  # 실측 소음 시각
        assert Bootstrapper._in_signal_window(self._at(14, "18:31")) is False

    def test_pre_market_blocked(self):
        assert Bootstrapper._in_signal_window(self._at(14, "08:59")) is False

    def test_weekend_blocked(self):
        assert Bootstrapper._in_signal_window(datetime(2026, 10, 17, 12, 0)) is False  # 토
        assert Bootstrapper._in_signal_window(datetime(2026, 10, 18, 12, 0)) is False  # 일

    def test_holiday_blocked(self):
        assert Bootstrapper._in_signal_window(datetime(2026, 10, 9, 12, 0)) is False  # 한글날

    def test_worker_gate_wired_before_analysis(self):
        src = (Path(__file__).resolve().parents[2] / "app" / "bootstrap.py").read_text(encoding="utf-8")
        gate = src.index("if not self._in_signal_window():")
        analysis = src.index("self._should_analyze(ticker", gate)
        assert gate < analysis, "장외 게이트가 분석 호출보다 앞에 있어야 한다"
        seg = src[gate:gate + 400]
        assert "task_done()" in seg and "continue" in seg
