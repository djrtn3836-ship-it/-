# -*- coding: utf-8 -*-
"""analytics/alert_verifier.py - v2.0 (Alert Verification, 신뢰 가능한 감사 기반)

배경(수정 사유):
    v1.0은 "Telegram 전송 수"를 `logs/telegram.log`에서 문자열 "SIGNAL_ENTRY"를
    찾아 세는 방식이었다. 그러나 실제 코드/로그 어디에도 그 문자열이 기록되지 않아
    **항상 0건 → "✅ 모든 신호 전송됨"이라는 거짓 보고**를 냈다.
    또한 "누락 종목" 목록도 전체 신호를 나열하는 버그가 있었다.

v2.0 설계:
    - TelegramSender.send()가 성공 시 `logs/alerts_audit.jsonl`에 1줄 기록한다.
    - 검증기는 DB 결정(당일)과 감사 로그를 **실제로 대조**한다.
    - 감사 로그가 없으면 "검증 불가"를 정직하게 보고한다(거짓 OK 금지).
"""

from __future__ import annotations

import json
from datetime import datetime
import os
from pathlib import Path
from typing import Any, Dict, List

from core.logger import setup_logger

logger = setup_logger("alert_verifier")

# P12-5: 테스트가 운영 감사로그를 오염시키지 않도록 경로를 환경변수로 격리 가능하게 한다.
AUDIT_PATH = Path(
    os.getenv("ALERTS_AUDIT_PATH", str(Path(__file__).parent.parent / "logs" / "alerts_audit.jsonl"))
)

# 진입/청산 "알림 대상" 결정으로 볼 action 집합
NOTIFIABLE_ACTIONS = {"SIGNAL_ENTRY", "BUY", "SELL"}


def load_audit_records(date_str: str, audit_path: Path = AUDIT_PATH) -> List[Dict[str, Any]]:
    """감사 로그에서 해당 날짜의 기록만 반환한다."""
    if not audit_path.exists():
        return []
    records: List[Dict[str, Any]] = []
    try:
        with open(audit_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(rec.get("date", "")) == date_str:
                    records.append(rec)
    except Exception as e:
        logger.warning(f"감사 로그 읽기 실패: {e}")
        return []
    return records


def compare_decisions_and_alerts(
    decisions: List[Dict[str, Any]], audit_records: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """당일 결정과 실제 발송 기록을 대조한다(순수 함수, 테스트 가능).

    Returns:
        {"total_decisions", "notifiable", "sent", "missing_tickers",
         "audit_available", "match_rate"}
    """
    notifiable = [d for d in decisions if str(d.get("action", "")).upper() in NOTIFIABLE_ACTIONS]
    sent_tickers = {str(r.get("ticker", "")) for r in audit_records}

    missing_tickers: List[str] = []
    for d in notifiable:
        ticker = str(d.get("ticker", ""))
        if ticker and ticker not in sent_tickers:
            missing_tickers.append(ticker)

    audit_available = bool(audit_records)
    match_rate = (
        (len(notifiable) - len(missing_tickers)) / len(notifiable) if notifiable else 1.0
    )
    return {
        "total_decisions": len(decisions),
        "notifiable": len(notifiable),
        "sent": len(audit_records),
        "missing_tickers": missing_tickers,
        "audit_available": audit_available,
        "match_rate": match_rate,
    }


async def verify_today_alerts() -> None:
    """당일 신호 발송 누락을 검증해 Telegram으로 보고한다."""
    from data.db_manager import DatabaseManager
    from report.telegram_sender import TelegramSender

    today = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"Verifying alerts for {today}")

    db = DatabaseManager()
    try:
        decisions = await db.get_decisions_by_date(today)
    except Exception as e:
        logger.error(f"결정 조회 실패: {e}")
        decisions = []
    finally:
        try:
            await db.close()
        except Exception:
            pass

    audit_records = load_audit_records(today)
    result = compare_decisions_and_alerts(decisions, audit_records)

    lines = [
        f"📊 <b>알림 검증 보고서 ({today})</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🗂 당일 결정: <b>{result['total_decisions']}</b>",
        f"📌 알림 대상(BUY/SELL/SIGNAL_ENTRY): <b>{result['notifiable']}</b>",
        f"📨 실제 발송(감사 로그): <b>{result['sent']}</b>",
    ]

    if not result["audit_available"] and result["notifiable"] > 0:
        lines.append("⚠️ 감사 로그 없음 — <b>검증 불가</b> (거짓 OK 보고 방지)")
    elif result["missing_tickers"]:
        lines.append(f"❌ 누락 의심: <b>{len(result['missing_tickers'])}</b>건")
        preview = ", ".join(result["missing_tickers"][:10])
        lines.append(f"   {preview}")
        lines.append(f"   일치율: {result['match_rate']:.1%}")
    else:
        lines.append(f"✅ 알림 대상 전건 발송 확인 (일치율 {result['match_rate']:.1%})")

    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"<i>🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>")

    try:
        await TelegramSender().send_raw("\n".join(lines))
    except Exception as e:
        logger.warning(f"검증 보고서 전송 실패: {e}")
    logger.info(
        f"Alert verification completed: notifiable={result['notifiable']} "
        f"sent={result['sent']} missing={len(result['missing_tickers'])}"
    )


async def scheduled_verify() -> None:
    """스케줄러 진입점(매일 16:00)."""
    await verify_today_alerts()
