"""로그 폭주·휴장일 재연결 폭주 회귀 테스트 (2026-10-09 휴장일 실측).

사고: 휴장일(한글날)에도 09:00~15:20 창이 열려 3분마다 강제 재연결 +
      per-tick DEBUG/WARNING 로그 → 하루 23MB.
      ① bootstrap: 거래일 검사 누락 (kiwoom_rest 4.2MB)
      ② shadow_mode: 틱마다 DEBUG (26,268줄/7MB)
      ③ anomaly_detector: 관측마다 WARNING (17,862줄/5.2MB)
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


# ── ① 휴장·주말 재연결 폭주 차단 ──────────────────────────────────────
class TestDataFlowWindow:

    def _win(self, *args):
        from app.bootstrap import Bootstrapper
        return Bootstrapper._in_data_flow_window(datetime(*args))

    def test_trading_day_market_hours_true(self):
        assert self._win(2026, 10, 8, 10, 0) is True      # 목(거래일)

    def test_holiday_false(self):
        assert self._win(2026, 10, 9, 10, 0) is False     # 한글날(휴장)

    def test_weekend_false(self):
        assert self._win(2026, 10, 10, 10, 0) is False    # 토
        assert self._win(2026, 10, 11, 10, 0) is False    # 일

    def test_before_open_and_after_window_false(self):
        assert self._win(2026, 10, 8, 8, 30) is False
        assert self._win(2026, 10, 8, 15, 25) is False    # 15:20 이후

    def test_window_edges(self):
        assert self._win(2026, 10, 8, 9, 0) is True
        assert self._win(2026, 10, 8, 15, 20) is False


# ── ② 섀도우 일치 로그 요약 ───────────────────────────────────────────
class TestShadowLogThrottle:

    def _runner(self):
        from application.analysis.shadow_mode import ShadowRunner
        from domain.models.signal import Action

        async def _shadow(_data):
            from domain.models.signal import Signal
            return Signal(ticker="005930", action=Action.HOLD, score=0.5, confidence=0.5, price=100.0)

        return ShadowRunner("low_vol_120d", _shadow)

    @pytest.mark.asyncio
    async def test_agreement_logged_once_per_interval(self, monkeypatch):
        import application.analysis.shadow_mode as sm
        from domain.models.signal import Action, Signal

        calls = []
        monkeypatch.setattr(sm.logger, "debug", lambda *a, **k: calls.append(a))

        runner = self._runner()
        prod = Signal(ticker="005930", action=Action.HOLD, score=0.5, confidence=0.5, price=100.0)
        for _ in range(5):
            await runner.run({}, prod)

        assert len(calls) == 1, f"일치 5회 → 요약 1회만 기록해야 함(실제 {len(calls)})"

    @pytest.mark.asyncio
    async def test_disagreement_always_logged(self, monkeypatch):
        import application.analysis.shadow_mode as sm
        from domain.models.signal import Action, Signal

        calls = []
        monkeypatch.setattr(sm.logger, "debug", lambda *a, **k: calls.append(a))

        runner = self._runner()
        prod = Signal(ticker="005930", action=Action.BUY, score=0.5, confidence=0.5, price=100.0)
        for _ in range(3):
            await runner.run({}, prod)

        assert len(calls) == 3, f"불일치 3회 → 3회 기록해야 함(실제 {len(calls)})"


# ── ③ 이상탐지 경고 쿨다운 ────────────────────────────────────────────
class TestAnomalyWarnCooldown:

    def test_warning_throttled_but_report_kept(self, monkeypatch):
        import observability.anomaly_detector as ad

        warns = []
        monkeypatch.setattr(ad.logger, "warning", lambda *a, **k: warns.append(a))

        class _StubForest:
            def anomaly_score(self, _sample):
                return 0.9

        det = ad.AnomalyDetector(window_size=30, threshold=0.65)
        det._forest = _StubForest()
        for _ in range(3):
            det.observe(0.5, 0.5, 10.0, 0.5)

        assert len(det.recent_anomalies()) == 3, "리포트는 계속 적재되어야 함(로그만 억제)"
        assert len(warns) == 1, f"쿨다운 내 경고는 1회만(실제 {len(warns)})"

    def test_cooldown_env_override(self):
        import observability.anomaly_detector as ad
        assert ad._ANOMALY_LOG_COOLDOWN_SEC > 0
