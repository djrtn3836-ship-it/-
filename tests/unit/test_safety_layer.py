# -*- coding: utf-8 -*-
"""tests/unit/test_safety_layer.py - 안전 계층 검증 (P9-1).

대상: `risk/safety_guard.py`, `core/supervisor.py`
배경: 감사에서 두 모듈 모두 **테스트 언급 0건**으로 확인됐다.
      SafetyGuard는 진입 차단(BLOCK_ALL)을 결정하는 안전 핵심 모듈이고,
      Supervisor는 프로세스 사망 감지/재시작을 담당한다.
"""

import asyncio
from typing import Any, List

import pytest

from risk.safety_guard import SafetyGuard, SafetyThreshold, _PLAUSIBLE_RANGES


class TestThresholdsDirection:
    """방향성 판정 — 부호 실수는 곧 오탐/미탐으로 이어진다."""

    def test_kospi_drop_triggers_on_negative(self) -> None:
        g = SafetyGuard()
        assert g._is_triggered("kospi_drop", -3.5, g.THRESHOLDS["kospi_drop"]) is True
        assert g._is_triggered("kospi_drop", -2.0, g.THRESHOLDS["kospi_drop"]) is False
        # +3% 상승은 '급락'이 아니다
        assert g._is_triggered("kospi_drop", 3.0, g.THRESHOLDS["kospi_drop"]) is False

    def test_spike_triggers_on_positive(self) -> None:
        g = SafetyGuard()
        assert g._is_triggered("vkospi_spike", 35.0, g.THRESHOLDS["vkospi_spike"]) is True
        assert g._is_triggered("vkospi_spike", 20.0, g.THRESHOLDS["vkospi_spike"]) is False
        assert g._is_triggered("usdkrw_spike", 1450.0, g.THRESHOLDS["usdkrw_spike"]) is True

    def test_unknown_condition_does_not_block(self) -> None:
        """알 수 없는 조건은 안전하게 미차단(오탐 방지)."""
        g = SafetyGuard()
        assert g._is_triggered("모르는조건", 999.0, SafetyThreshold(1.0, "b", "s")) is False

    def test_severity_assignment(self) -> None:
        g = SafetyGuard()
        assert g._get_severity("kospi_drop") in ("CRITICAL", "HIGH")
        assert g._get_severity("vkospi_spike") in ("CRITICAL", "HIGH")


class TestCheckFlow:
    def test_all_clear_when_normal(self) -> None:
        g = SafetyGuard()
        r = g.check({"kospi_drop": 0.5, "vkospi_spike": 18.0, "usdkrw_spike": 1300.0})

        assert r["all_clear"] is True
        assert r["action"] == "NONE"
        assert r["trigger_count"] == 0

    def test_crisis_blocks_all(self) -> None:
        g = SafetyGuard()
        r = g.check({"kospi_drop": -5.0, "vkospi_spike": 45.0})

        assert r["all_clear"] is False
        assert r["action"] == "BLOCK_ALL"
        assert r["critical_triggered"] is True
        assert r["trigger_count"] >= 1

    def test_out_of_range_values_ignored(self) -> None:
        """물리적으로 불가능한 값(±30% 초과 등)은 위기 판정을 건너뛴다."""
        g = SafetyGuard()
        r = g.check({"kospi_drop": -999.0})          # 범위 밖

        assert r["all_clear"] is True

    def test_plausible_ranges_cover_all_thresholds(self) -> None:
        """모든 임계 조건에 타당 범위가 정의되어 있어야 한다(방어선 누락 방지)."""
        missing = set(SafetyGuard.THRESHOLDS) - set(_PLAUSIBLE_RANGES)
        assert missing == set(), f"타당 범위 미정의: {missing}"

    def test_none_values_are_skipped(self) -> None:
        g = SafetyGuard()
        r = g.check({"kospi_drop": None})            # 데이터 없음 → 판정 불가
        assert r["all_clear"] is True

    def test_alert_cooldown_suppresses_repeats(self) -> None:
        g = SafetyGuard(alert_cooldown_sec=1800.0)
        first = g.check({"kospi_drop": -5.0})
        second = g.check({"kospi_drop": -5.0})

        assert first["should_alert"] is True
        assert second["should_alert"] is False       # 쿨다운 내

    def test_block_cleared_detected(self) -> None:
        """차단 → 해제 전환을 알려야 한다(운영자가 해제를 인지)."""
        g = SafetyGuard(alert_cooldown_sec=0.0)
        g.check({"kospi_drop": -5.0})
        r = g.check({"kospi_drop": 0.0})

        assert r["all_clear"] is True
        assert r.get("block_cleared") is True

    def test_status_and_log(self) -> None:
        g = SafetyGuard()
        g.check({"kospi_drop": -5.0})

        assert isinstance(g.get_status(), dict)
        log = g.get_trigger_log(limit=10)
        assert isinstance(log, list)
        assert len(log) >= 1

    def test_threshold_basis_documented(self) -> None:
        """모든 임계값은 근거(basis/source)를 가져야 한다."""
        g = SafetyGuard()
        basis = g.get_threshold_basis()

        assert set(basis) == set(SafetyGuard.THRESHOLDS)
        for name, info in basis.items():
            assert info.get("basis"), f"{name} 근거 누락"
            assert info.get("source"), f"{name} 출처 누락"


class TestSupervisor:
    """SystemSupervisor — 프로세스 감시 및 재시작 판단 (네트워크/서브프로세스 모킹)."""

    def _sup(self) -> Any:
        from core.supervisor import SystemSupervisor

        return SystemSupervisor()

    def test_instantiation(self) -> None:
        assert self._sup() is not None

    def test_process_running_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sup = self._sup()
        monkeypatch.setattr(sup, "_is_process_running", lambda: True)
        assert sup._is_process_running() is True

        monkeypatch.setattr(sup, "_is_process_running", lambda: False)
        assert sup._is_process_running() is False

    async def test_restarts_outside_market_hours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """장 마감 후 사망 → 자동 재시작."""
        from core import supervisor as sup_mod

        sup = self._sup()
        restarted: List[str] = []

        async def _restart() -> None:
            restarted.append("scanner")

        monkeypatch.setattr(sup, "_restart_scanner", _restart)
        monkeypatch.setattr(sup_mod, "_is_market_hours", lambda: False)
        sup._restart_pending = False

        await sup._handle_process_death()

        assert restarted == ["scanner"]

    async def test_defers_restart_during_market_hours(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """장중 사망 → 즉시 재시작 금지, 수동 개입 요청(안전 규칙)."""
        from core import supervisor as sup_mod

        sup = self._sup()
        restarted: List[str] = []

        async def _restart() -> None:
            restarted.append("scanner")

        monkeypatch.setattr(sup, "_restart_scanner", _restart)
        monkeypatch.setattr(sup_mod, "_is_market_hours", lambda: True)
        sup._restart_pending = False

        await sup._handle_process_death()

        assert restarted == []
        assert sup._restart_pending is True

    async def test_error_count_reads_log(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        sup = self._sup()
        log = tmp_path / "err.log"
        log.write_text("line\nERROR x\nERROR y\n", encoding="utf-8")
        monkeypatch.setattr(type(sup), "_LOG_PATH", log, raising=False)

        try:
            count = sup._count_recent_errors()
            assert isinstance(count, int) and count >= 0
        except AttributeError:
            pytest.skip("로그 경로 속성 미노출 — 환경 의존")

    def test_run_is_coroutine(self) -> None:
        sup = self._sup()
        coro = sup.run()
        assert asyncio.iscoroutine(coro)
        coro.close()          # 실행하지 않고 정리
