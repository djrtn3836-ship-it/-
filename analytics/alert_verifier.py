# -*- coding: utf-8 -*-
"""
analytics/alert_verifier.py - v1.0 (Alert Verification)
- Verifies that all signals generated are sent via Telegram
- Compares decisions in DB with Telegram logs
- Sends a summary report at 16:00 daily
"""

from datetime import datetime
from pathlib import Path

from core.logger import setup_logger
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender

logger = setup_logger("alert_verifier")
telegram = TelegramSender()
db = DatabaseManager()


async def verify_today_alerts():
    """Verify that all today's signals were sent via Telegram"""
    today = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"Verifying alerts for {today}")

    # 1. Get all SIGNAL_ENTRY decisions from DB
    decisions = await db.get_decisions_by_date(today)
    signal_entries = [d for d in decisions if d.get("action") == "SIGNAL_ENTRY"]
    total_signals = len(signal_entries)

    # 2. Count Telegram sends from logs (telegram.log)
    log_path = Path(__file__).parent.parent / "logs" / "telegram.log"
    sent_count = 0
    if log_path.exists():
        with open(log_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "SIGNAL_ENTRY" in line and today in line:
                    sent_count += 1

    # 3. Build report
    msg = (
        f"📊 <b>Alert Verification Report ({today})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Total signals: <b>{total_signals}</b>\n"
        f"📨 Telegram sent: <b>{sent_count}</b>\n"
        f"❌ Missing: <b>{total_signals - sent_count}</b>\n"
    )

    if total_signals > sent_count:
        msg += "⚠️ <b>Some signals were not sent!</b>\n"
        # List missing tickers
        missing = [
            d.get("ticker") for d in signal_entries if d.get("action") == "SIGNAL_ENTRY"
        ]
        msg += f"Missing tickers: {', '.join(set(missing)[:10])}\n"
        msg += "Check logs: logs/scanner.log, logs/telegram.log\n"
    else:
        msg += "✅ All signals were sent successfully!\n"

    msg += "━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"<i>🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"

    await telegram.send_raw(msg)
    logger.info("Alert verification completed")


# Scheduled verification (daily at 16:00)
async def scheduled_verify():
    await verify_today_alerts()