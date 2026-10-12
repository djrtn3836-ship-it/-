"""중복 실행·오류 폭주 방지 테스트 (2026-10-10 Session 109 — 실사고 대응).

사고: PID 가드가 뚫려 4개 인스턴스 동시 실행 → 봇 폴링 Conflict +
SQLite lock → 오류 텔레그램 폭주(2026-10-12).
"""
import asyncio
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class _Boot:
    """Bootstrapper.__new__ 경량 인스턴스 + 최소 의존성."""

    def __init__(self):
        sent = []

        class _S:
            async def send_raw(self, text, **kw):
                sent.append(text)

        self._error_sender = _S()
        self._sent = sent

    def _send(self, msg, detail=""):
        return asyncio.run(self._send_error_alert(msg, detail))


def _mk():
    from app.bootstrap import Bootstrapper

    b = _Boot()
    Bootstrapper.__init__.__get__(b) if False else None
    f = Bootstrapper._send_error_alert
    import types

    b._send_error_alert = types.MethodType(f, b)
    return b


class TestErrorAlertThrottle:

    def test_same_error_suppressed(self):
        b = _mk()
        b._send("Worker-1 error", "database is locked")
        b._send("Worker-1 error", "database is locked")
        assert len(b._sent) == 1, "동일 오류 15분 쿨다운이 동작하지 않음"

    def test_different_error_passes(self):
        b = _mk()
        b._send("Worker-1 error", "database is locked")
        b._send("Worker-2 error", "database is locked")
        assert len(b._sent) == 2

    def test_global_cap_10_per_10min(self):
        b = _mk()
        for i in range(25):
            b._send(f"Unique error {i}", f"detail {i}")
        assert len(b._sent) == 10, "10분 10건 상한이 동작하지 않음"


class TestInstanceLock:

    def test_second_instance_exits_immediately(self):
        """main.py 최상단 락 — 점유된 포트면 즉시 SystemExit(0)."""
        import os as _os

        _os.environ["INSTANCE_LOCK_PORT"] = "47691"  # 실운영(47653)과 격리
        from app.main import acquire_instance_lock

        hold = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        hold.bind(("127.0.0.1", 47691))
        hold.listen(1)
        try:
            try:
                acquire_instance_lock()
                raised = False
            except SystemExit as e:
                raised = True
                assert e.code == 0  # 스케줄러가 실패로 오인하지 않도록 0
            assert raised, "이미 점유된 락인데 중복 인스턴스가 종료되지 않음"
        finally:
            hold.close()

    def test_lock_source_guard(self):
        root = Path(__file__).resolve().parents[2]
        src_main = (root / "app" / "main.py").read_text(encoding="utf-8")
        # 락은 main.py 최상단, 부트스트랩 이전에 있어야 한다(사고 재발 방지)
        assert "47653" in src_main and "acquire_instance_lock()" in src_main
        assert src_main.index("acquire_instance_lock()") < src_main.index("Bootstrapper()")
        src = (root / "app" / "bootstrap.py").read_text(encoding="utf-8")
        assert "_error_alert_last" in src and "_error_alert_stamps" in src
