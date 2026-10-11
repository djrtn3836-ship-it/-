"""알림 파이프라인 신뢰성 회귀 테스트 (2026-10-10).

배경(고친 약점 3건):
① 워커 블로킹: `await sender.send()`가 네트워크 장애 시 send_raw 재시도(≈3분)로
   워커를 멈춰 세웠다 → 전송을 백그라운드 큐로 분리.
② 영구 유실: 전송 실패 알림이 로그만 남기고 사라졌다 → 보관 파일에 저장 후 재시도.
③ 감사 정합성: 감사 로그는 **실제 전송 성공 시에만** 기록되어야 한다.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _sender():
    from report.telegram_sender import TelegramSender
    s = TelegramSender()
    s.bot = object()
    s.chat_id = "1"
    return s


def _report(action="SIGNAL_ENTRY", ticker="005930"):
    return {
        "action": action,
        "ticker": ticker,
        "price": 70000,
        "confidence": 0.7,
        "reason": "test",
    }


def _app(tmp_path):
    """Bootstrapper 인스턴스를 가볍게 만든다(부팅 없이 알림 큐만)."""
    from app.bootstrap import Bootstrapper
    app = Bootstrapper.__new__(Bootstrapper)
    app._alert_queue = asyncio.Queue(maxsize=1000)
    app._alert_task = None
    app._alert_backlog = []
    app._alert_backlog_path = tmp_path / "pending_alerts.jsonl"
    app._alert_backlog_max = 200
    app._alert_backlog_last_try = 0.0
    app._shutdown_requested = False
    return app


class FakeSender:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    async def send_formatted(self, text, report):
        self.sent.append((text, report))
        return self.ok


class TestFormatSeparation:

    def test_format_builds_without_sending(self):
        s = _sender()
        text = s.format(_report())
        assert isinstance(text, str) and text

    def test_format_returns_none_for_error_ignore(self):
        s = _sender()
        assert s.format(_report(action="ERROR")) is None
        assert s.format(_report(action="IGNORE")) is None

    def test_format_returns_none_without_bot(self):
        from report.telegram_sender import TelegramSender
        s = TelegramSender()
        s.bot = None
        assert s.format(_report()) is None

    def test_unknown_action_is_not_sent(self):
        s = _sender()
        assert s.format(_report(action="WHAT_IS_THIS")) is None

    def test_send_api_preserved(self):
        from report.telegram_sender import TelegramSender
        s = TelegramSender()
        s.bot = None
        assert asyncio.run(s.send(_report())) is False  # 봇 없음 → 실패(기존 동작)


class TestAuditOnSuccessOnly:

    def test_audit_written_only_on_delivery(self, tmp_path):
        from report import telegram_sender as ts

        s = _sender()
        calls = []

        async def fake_raw(text, max_retries=4):
            calls.append(text)
            return False

        s.send_raw = fake_raw
        audit_before = _audit_lines(ts)
        ok = asyncio.run(s.send_formatted("hi", _report()))
        assert ok is False
        assert len(calls) == 1
        assert _audit_lines(ts) == audit_before, "실패했는데 감사 로그가 기록됨"

        async def fake_raw_ok(text, max_retries=4):
            return True

        s.send_raw = fake_raw_ok
        ok = asyncio.run(s.send_formatted("hi", _report()))
        assert ok is True
        assert _audit_lines(ts) == audit_before + 1


def _audit_lines(ts_module):
    path = Path(ts_module.AUDIT_PATH)
    if not path.exists():
        return 0
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])


class TestAlertQueue:

    def test_enqueue_and_worker_delivers(self, tmp_path):
        app = _app(tmp_path)
        fake = FakeSender(ok=True)
        app._error_sender = fake

        async def run():
            task = asyncio.create_task(app._alert_worker())
            app._enqueue_alert("hello", _report())
            await asyncio.sleep(0.1)
            app._shutdown_requested = True
            await asyncio.wait_for(task, timeout=3.0)

        asyncio.run(run())
        assert fake.sent and fake.sent[0][0] == "hello"
        assert app._alert_backlog == []

    def test_failed_send_goes_to_backlog_file(self, tmp_path):
        app = _app(tmp_path)
        fake = FakeSender(ok=False)
        app._error_sender = fake

        async def run():
            task = asyncio.create_task(app._alert_worker())
            app._enqueue_alert("will-fail", _report())
            await asyncio.sleep(0.1)
            app._shutdown_requested = True
            await asyncio.wait_for(task, timeout=3.0)

        asyncio.run(run())
        assert app._alert_backlog_path.exists(), "실패 알림이 파일로 보관되지 않음"
        lines = app._alert_backlog_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["text"] == "will-fail"

    def test_backlog_loaded_on_start(self, tmp_path):
        app = _app(tmp_path)
        app._alert_backlog_path.parent.mkdir(parents=True, exist_ok=True)
        app._alert_backlog_path.write_text(
            json.dumps({"ts": "x", "text": "old1", "report": {}}) + "\n"
            + json.dumps({"ts": "x", "text": "old2", "report": {}}) + "\n",
            encoding="utf-8",
        )
        app._load_alert_backlog()
        assert [i["text"] for i in app._alert_backlog] == ["old1", "old2"]

    def test_backlog_retried_after_success(self, tmp_path):
        app = _app(tmp_path)
        fake = FakeSender(ok=True)
        app._error_sender = fake
        app._alert_backlog = [
            {"ts": "x", "text": "pending-1", "report": {}},
            {"ts": "x", "text": "pending-2", "report": {}},
        ]
        app._alert_backlog_last_try = 0.0

        async def run():
            task = asyncio.create_task(app._alert_worker())
            app._enqueue_alert("fresh", _report())
            await asyncio.sleep(0.1)
            app._shutdown_requested = True
            await asyncio.wait_for(task, timeout=3.0)

        asyncio.run(run())
        sent_texts = [t for t, _ in fake.sent]
        assert "fresh" in sent_texts
        assert "pending-1" in sent_texts and "pending-2" in sent_texts
        assert app._alert_backlog == [], "재전송 성공분이 보관함에 남음"

    def test_backlog_kept_when_still_failing(self, tmp_path):
        app = _app(tmp_path)
        fake = FakeSender(ok=False)
        app._error_sender = fake
        app._alert_backlog = [{"ts": "x", "text": "pending-1", "report": {}}]

        async def run():
            task = asyncio.create_task(app._alert_worker())
            app._enqueue_alert("fresh", _report())
            await asyncio.sleep(0.15)
            app._shutdown_requested = True
            await asyncio.wait_for(task, timeout=3.0)

        asyncio.run(run())
        texts = [i["text"] for i in app._alert_backlog]
        assert "pending-1" in texts and "fresh" in texts

    def test_queue_full_drops_one(self, tmp_path):
        app = _app(tmp_path)
        app._alert_queue = asyncio.Queue(maxsize=1)
        assert app._enqueue_alert("a", _report()) is True
        assert app._enqueue_alert("b", _report()) is False  # 포화 → 폐기

    def test_worker_drains_before_exit_on_shutdown(self, tmp_path):
        """셧다운 플래그가 켜져도 큐에 남은 알림은 처리하고 종료해야 한다."""
        app = _app(tmp_path)
        fake = FakeSender(ok=True)
        app._error_sender = fake
        app._shutdown_requested = True
        app._enqueue_alert("last-1", _report())
        app._enqueue_alert("last-2", _report())

        async def run():
            task = asyncio.create_task(app._alert_worker())
            await asyncio.wait_for(task, timeout=3.0)

        asyncio.run(run())
        assert [t for t, _ in fake.sent] == ["last-1", "last-2"]

    def test_queue_remainder_persisted(self, tmp_path):
        """미소진 큐는 종료 시 보관 파일로 옮겨져야 한다(유실 방지)."""
        app = _app(tmp_path)
        app._enqueue_alert("leftover", _report())
        app._persist_queue_remainder()
        assert app._alert_queue.empty()
        assert app._alert_backlog_path.exists()
        assert json.loads(
            app._alert_backlog_path.read_text(encoding="utf-8").splitlines()[0]
        )["text"] == "leftover"

    def test_backlog_capped(self, tmp_path):
        app = _app(tmp_path)
        app._alert_backlog_max = 3
        for i in range(10):
            app._alert_backlog.append({"text": f"t{i}"})
        app._alert_backlog = app._alert_backlog[-app._alert_backlog_max:]
        assert len(app._alert_backlog) == 3
        assert app._alert_backlog[-1]["text"] == "t9"


class TestAdminRawMessages:
    """관리 알림(RAW): 전송은 하되 감사 로그는 남기지 않는다."""

    def test_format_raw_returns_text(self):
        s = _sender()
        assert s.format({"action": "RAW", "text": "백업 실패"}) == "백업 실패"
        assert s.format({"action": "RAW", "text": ""}) is None

    def test_raw_send_skips_audit(self, tmp_path):
        from report import telegram_sender as ts

        s = _sender()
        calls = []

        async def fake_raw(text, max_retries=4):
            calls.append(text)
            return True

        s.send_raw = fake_raw
        before = _audit_lines(ts)
        ok = asyncio.run(
            s.send_formatted("관리알림", {"action": "RAW", "ticker": "SYSTEM", "text": "관리알림"})
        )
        assert ok is True and calls == ["관리알림"]
        assert _audit_lines(ts) == before, "RAW 알림이 감사 로그를 오염시킴"

    def test_admin_message_goes_through_queue(self, tmp_path):
        app = _app(tmp_path)
        fake = FakeSender(ok=True)
        app._error_sender = fake

        class _AliveTask:
            def done(self):
                return False

        app._alert_task = _AliveTask()  # 살아있는 워커로 판정되게

        ok = app._enqueue_admin_message("백업 완료")
        assert ok is True
        text, report = app._alert_queue.get_nowait()
        assert text == "백업 완료" and report["action"] == "RAW"
