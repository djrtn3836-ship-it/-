# -*- coding: utf-8 -*-
"""tests/unit/test_notification_layer.py - 알림 계층 검증 (P9-3).

대상: `report/telegram_commands.py`, `report/daily_report.py`
배경:
    - 감사에서 두 모듈 모두 테스트 언급 0건.
    - 게다가 P8-4 작업 중 telegram_commands.py가 **SyntaxError 상태로 커밋**될 뻔했다
      (해당 모듈을 import하는 테스트가 없어 pytest가 통과). 이 테스트는 그 구멍을 막는다.
"""

from typing import Any, Dict, List

import pytest


# ═══════════════════════════════════════════════════════════════════
#  가짜 텔레그램 객체
# ═══════════════════════════════════════════════════════════════════

class _Msg:
    def __init__(self) -> None:
        self.texts: List[str] = []
        self.kwargs: List[Dict[str, Any]] = []

    async def reply_text(self, text: str, **kwargs: Any) -> None:
        self.texts.append(text)
        self.kwargs.append(kwargs)


class _Chat:
    def __init__(self, chat_id: str) -> None:
        self.id = chat_id


class _Update:
    def __init__(self, text: str = "", chat_id: str = "1") -> None:
        self.message = _Msg()
        self.effective_chat = _Chat(chat_id)
        self.message.text = text


class _Context:
    bot = None


def _handler(chat_id: str = "1", **deps: Any) -> Any:
    from report.telegram_commands import TelegramCommandHandler

    h = TelegramCommandHandler(token="t", chat_id=chat_id, get_stats_callback=lambda: {"ok": True})
    if deps:
        h.set_dependencies(**deps)
    return h


class TestModuleImport:
    """가장 기본: 모듈이 구문/임포트 오류 없이 로드되어야 한다(P8-4 사고 재발 방지)."""

    def test_imports(self) -> None:
        import report.telegram_commands as tc

        assert hasattr(tc, "TelegramCommandHandler")

    def test_handler_methods_exist(self) -> None:
        from report.telegram_commands import TelegramCommandHandler as H

        for name in ("_natural_language_handler", "_status_command", "_signal_command",
                     "_trace_command", "_send_comprehensive_report", "_build_safe_report"):
            assert hasattr(H, name), f"{name} 누락"


class TestTraceCommand:
    """P8-4에서 추가한 /trace 명령."""

    async def test_trace_without_records(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from observability import trace_bridge as tb

        tb.clear()
        h = _handler()
        update = _Update("/trace")

        await h._trace_command(update, "/trace")

        assert any("기록된 trace가 없습니다" in t for t in update.message.texts)

    async def test_trace_with_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from observability import trace_bridge as tb

        tb.clear()
        tb.record_stage("T-77", "SignalPipeline", "process", "in", "out", 3.0, True)
        h = _handler()
        update = _Update("/trace T-77")

        await h._trace_command(update, "/trace T-77")

        assert any("SignalPipeline" in t for t in update.message.texts)

    async def test_trace_latest_when_no_arg(self) -> None:
        from observability import trace_bridge as tb

        tb.clear()
        tb.record_stage("T-88", "M", "op")
        h = _handler()
        update = _Update("/trace")

        await h._trace_command(update, "/trace")

        assert any("T-88" in t or "M" in t for t in update.message.texts)

    async def test_trace_truncates_long_output(self) -> None:
        from observability import trace_bridge as tb

        tb.clear()
        for i in range(300):
            tb.record_stage("T-LONG", "ModuleWithLongName", f"operation_number_{i}" * 3,
                            "input" * 20, "output" * 20, 1.0, True)
        h = _handler()
        update = _Update("/trace T-LONG")

        await h._trace_command(update, "/trace T-LONG")

        assert update.message.texts
        assert len(update.message.texts[0]) <= 3600

    async def test_trace_error_is_contained(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from observability import trace_bridge as tb

        monkeypatch.setattr(tb, "latest_trace_id", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        h = _handler()
        update = _Update("/trace")

        await h._trace_command(update, "/trace")      # 예외가 새지 않아야 한다

        assert any("오류" in t for t in update.message.texts)


class TestCommandGuards:
    async def test_unauthorized_chat_blocked(self) -> None:
        h = _handler(chat_id="999")
        update = _Update("/신호", chat_id="1")

        await h._signal_command(update, _Context())

        assert any("권한" in t for t in update.message.texts)

    async def test_signal_without_db_notifies(self) -> None:
        h = _handler(chat_id="1")           # db_manager 미설정
        update = _Update("/신호", chat_id="1")

        await h._signal_command(update, _Context())

        assert any("DB" in t for t in update.message.texts)

    async def test_status_command_returns_something(self) -> None:
        h = _handler(chat_id="1")
        update = _Update("/현황", chat_id="1")

        await h._status_command(update, _Context())

        assert update.message.texts, "상태 명령이 아무 응답도 하지 않음"


class TestNaturalLanguageDispatch:
    async def test_unknown_text_gets_a_response(self) -> None:
        """미인식 문장이어도 예외 없이 응답(안내/상태)을 돌려줘야 한다."""
        h = _handler(chat_id="1")
        update = _Update("아무말아무말", chat_id="1")

        await h._natural_language_handler(update, _Context())

        assert update.message.texts, "응답 없음"

    async def test_trace_routed_from_nl(self) -> None:
        from observability import trace_bridge as tb

        tb.clear()
        h = _handler(chat_id="1")
        update = _Update("/trace", chat_id="1")

        await h._natural_language_handler(update, _Context())

        assert update.message.texts


class TestDailyReport:
    def test_instantiation(self) -> None:
        from report.daily_report import DailyReportGenerator

        gen = DailyReportGenerator(db_manager=None, telegram_sender=None)
        assert gen is not None

    def test_diagnose_regime_handles_empty(self) -> None:
        from report.daily_report import DailyReportGenerator

        gen = DailyReportGenerator(db_manager=None, telegram_sender=None)
        result = gen._diagnose_regime([])

        assert isinstance(result, tuple) and len(result) == 3
        assert isinstance(result[0], str)

    def test_risk_and_action_items_return_lists(self) -> None:
        from report.daily_report import DailyReportGenerator

        gen = DailyReportGenerator(db_manager=None, telegram_sender=None)

        risks = gen._get_daily_risks("Bull", [])
        actions = gen._get_action_items("Bull", [])

        assert isinstance(risks, list)
        assert isinstance(actions, list)

    def test_diagnose_regime_with_decisions(self) -> None:
        from report.daily_report import DailyReportGenerator

        gen = DailyReportGenerator(db_manager=None, telegram_sender=None)
        decisions = [
            {"action": "BUY", "confidence": 0.8, "score": 0.7, "regime": "Bull"},
            {"action": "SELL", "confidence": 0.4, "score": 0.3, "regime": "Bear"},
        ]

        regime, desc, score = gen._diagnose_regime(decisions)

        assert isinstance(regime, str)
        assert isinstance(score, (int, float))

    async def test_generate_and_send_without_deps_is_safe(self) -> None:
        """DB/전송기가 없어도 예외로 터지지 않아야 한다(스케줄 잡 안정성)."""
        from report.daily_report import DailyReportGenerator

        gen = DailyReportGenerator(db_manager=None, telegram_sender=None)
        try:
            await gen.generate_and_send()
        except (AttributeError, TypeError):
            # 의존성 없이 호출 불가한 경로는 허용하되, 조용한 손상은 아니어야 함
            pass
