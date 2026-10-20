# -*- coding: utf-8 -*-
"""S152 신규 명령(/stress, /portfolio) 포맷 검증."""
import json

import pytest

from report.telegram_commands import TelegramCommandHandler


class Msg:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)


class Chat:
    id = 1


class Update:
    def __init__(self):
        self.effective_chat = Chat()
        self.message = Msg()


def _handler(tmp_path, monkeypatch):
    h = TelegramCommandHandler("dummy", "1", lambda: {})
    monkeypatch.setattr(h, "_data_path", lambda name: tmp_path / name)
    return h


@pytest.mark.asyncio
async def test_stress_command(tmp_path, monkeypatch):
    (tmp_path / "disclosure_strategy.json").write_text(json.dumps({
        "variants": {"A_all": {"portfolio": {"cagr": 30.2, "sharpe": 1.3, "mdd": 28.9}}},
        "capacity": {"median_krw": 216_000_000, "portfolio_krw": 1_080_000_000},
    }, ensure_ascii=False), encoding="utf-8")
    h = _handler(tmp_path, monkeypatch)
    u = Update()
    await h._stress_command(u, None)
    assert "CAGR 30.2%" in u.message.sent[0] and "2.16억" in u.message.sent[0]


@pytest.mark.asyncio
async def test_portfolio_command_missing_state(tmp_path, monkeypatch):
    h = _handler(tmp_path, monkeypatch)
    u = Update()
    await h._portfolio_command(u, None)
    assert "오류" in u.message.sent[0]


@pytest.mark.asyncio
async def test_portfolio_command_escapes_html(tmp_path, monkeypatch):
    (tmp_path / "disclosure_portfolio_state.json").write_text(json.dumps({
        "start": "2026-10-20", "cash": 0.8,
        "positions": [{"ticker": "A", "name": "<b>x</b>", "category": "자사주매입",
                       "entry_date": "2026-10-20", "entry_price": 1000, "hold": 5}],
        "closed": [], "equity": {"2026-10-20": 1.0},
    }, ensure_ascii=False), encoding="utf-8")
    h = _handler(tmp_path, monkeypatch)
    u = Update()
    await h._portfolio_command(u, None)
    assert "&lt;b&gt;x&lt;/b&gt;" in u.message.sent[0]
