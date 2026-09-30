# -*- coding: utf-8 -*-
"""tests/unit/test_alert_verifier.py - 알림 누락 검증기 v2.0 검증.

배경: v1.0은 로그에서 존재하지 않는 문자열("SIGNAL_ENTRY")을 세어
항상 "✅ 모든 신호 전송됨"이라는 거짓 보고를 냈다.
v2.0은 감사 로그(JSONL) 기반으로 DB 결정과 실제 발송을 대조한다.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from analytics.alert_verifier import (
    compare_decisions_and_alerts,
    load_audit_records,
)


def _decision(ticker: str, action: str) -> Dict[str, Any]:
    return {"ticker": ticker, "action": action}


class TestCompare:
    def test_all_sent(self) -> None:
        decisions = [_decision("005930", "BUY"), _decision("000660", "SELL")]
        audit = [{"ticker": "005930", "kind": "SIGNAL_ENTRY"}, {"ticker": "000660", "kind": "SIGNAL_ENTRY"}]

        result = compare_decisions_and_alerts(decisions, audit)

        assert result["notifiable"] == 2
        assert result["sent"] == 2
        assert result["missing_tickers"] == []
        assert result["match_rate"] == pytest.approx(1.0)
        assert result["audit_available"] is True

    def test_detects_missing(self) -> None:
        decisions = [_decision("005930", "BUY"), _decision("000660", "BUY")]
        audit = [{"ticker": "005930"}]

        result = compare_decisions_and_alerts(decisions, audit)

        assert result["missing_tickers"] == ["000660"]
        assert result["match_rate"] == pytest.approx(0.5)

    def test_no_audit_but_signals_exist(self) -> None:
        """감사 로그가 없으면 '검증 불가'로 판정되어야 한다(거짓 OK 금지)."""
        decisions = [_decision("005930", "BUY")]
        result = compare_decisions_and_alerts(decisions, [])
        assert result["audit_available"] is False
        assert result["notifiable"] == 1
        assert result["sent"] == 0

    def test_non_notifiable_actions_ignored(self) -> None:
        decisions = [_decision("005930", "HOLD"), _decision("000660", "ERROR")]
        result = compare_decisions_and_alerts(decisions, [])
        assert result["notifiable"] == 0
        assert result["match_rate"] == pytest.approx(1.0)

    def test_case_insensitive_action(self) -> None:
        decisions = [_decision("005930", "buy")]
        result = compare_decisions_and_alerts(decisions, [{"ticker": "005930"}])
        assert result["notifiable"] == 1
        assert result["missing_tickers"] == []


class TestLoadAudit:
    def _write(self, path: Path, records: List[Dict[str, Any]]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def test_filters_by_date_and_skips_bad_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "audit.jsonl"
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"date": "2026-09-30", "ticker": "005930"}) + "\n")
            f.write("{not valid json}\n")
            f.write(json.dumps({"date": "2026-09-29", "ticker": "000660"}) + "\n")
            f.write("\n")

        records = load_audit_records("2026-09-30", audit_path=p)

        assert len(records) == 1
        assert records[0]["ticker"] == "005930"

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_audit_records("2026-09-30", audit_path=tmp_path / "nope.jsonl") == []
