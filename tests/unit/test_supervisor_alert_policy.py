"""Supervisor 알림 정책 회귀 테스트 (2026-10-07 텔레그램 도배 사고).

사고: memory_threshold_mb=1024 하드코딩 + 쿨다운 부재 →
32GB PC에서 1.6GB(정상) 사용인데 30초마다 알림이 발송됐다.
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _make(monkeypatch=None, **env):
    import os
    for k, v in env.items():
        os.environ[k] = v
    import core.supervisor as sup
    importlib.reload(sup)
    return sup.SystemSupervisor()


def test_default_threshold_scales_with_system_ram():
    """기본 임계값은 시스템 RAM의 80% 수준(>=4GB)이어야 한다."""
    import psutil
    s = _make()
    total_mb = psutil.virtual_memory().total / (1024 * 1024)
    assert s.memory_threshold_mb >= 4096
    assert s.memory_threshold_mb <= total_mb


def test_env_override_threshold():
    s = _make(SUPERVISOR_MEMORY_THRESHOLD_MB="2048")
    assert s.memory_threshold_mb == 2048


def test_normal_usage_does_not_alert():
    """1.6GB는 어떤 합리적 임계값에서도 알림 대상이 아니어야 한다."""
    s = _make()
    assert not (1638 > s.memory_threshold_mb)


def test_should_alert_cooldown():
    s = _make()
    assert s._should_alert("memory") is True
    assert s._should_alert("memory") is False      # 쿨다운 내 재발송 금지
    assert s._should_alert("errors") is True       # 다른 종류는 독립


def test_alert_cooldown_env():
    s = _make(SUPERVISOR_ALERT_COOLDOWN_SEC="60")
    assert s.alert_cooldown_sec == 60
