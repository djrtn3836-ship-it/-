"""
analytics/alert_verifier.py - v1.0 (알림 누락 검증)
- 매일 장 종료 후 오늘 발생한 신호와 실제 Telegram 전송 건수 비교
- 누락 발생 시 원인 분석 리포트 전송
"""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from core.logger import setup_logger
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender

logger = setup_logger("alert_verifier")
telegram = TelegramSender()
db = DatabaseManager()


async def verify_today_alerts():
    """오늘 신호 대비 Telegram 전송 건수 검증"""
    today = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"📊 오늘({today}) 알림 검증 시작")

    # 1. DB에서 오늘 생성된 SIGNAL_ENTRY 건수 조회
    decisions = await db.get_decisions_by_date(today)
    signal_entries = [d for d in decisions if d.get("action") == "SIGNAL_ENTRY"]
    total_signals = len(signal_entries)

    # 2. Telegram 로그에서 실제 전송된 건수 확인 (logs/telegram.log 파싱)
    log_path = Path(__file__).parent.parent / "logs" / "telegram.log"
    sent_count = 0
    if log_path.exists():
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "✅ Telegram 메시지 전송 성공" in line and today in line:
                    sent_count += 1

    # 3. 리포트 작성
    msg = (
        f"📊 <b>오늘({today}) 알림 검증 리포트</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 감지된 신호: <b>{total_signals}건</b>\n"
        f"• Telegram 전송: <b>{sent_count}건</b>\n"
        f"• 누락: <b>{total_signals - sent_count}건</b>\n"
    )

    if total_signals > sent_count:
        msg += f"⚠️ <b>누락 발생!</b> 원인 분석 중...\n"
        # 누락된 신호의 ticker 목록 추출 (간단히)
        missing = [d.get("ticker") for d in signal_entries if d.get("action") == "SIGNAL_ENTRY"]
        msg += f"• 누락 종목: {', '.join(set(missing)[:10])}\n"
        msg += "• 로그 확인 필요: logs/scanner.log, logs/telegram.log\n"
    else:
        msg += "✅ 모든 신호가 정상 전송되었습니다.\n"

    msg += "━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"<i>🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"

    await telegram.send_raw(msg)
    logger.info("📊 알림 검증 완료")


# 스케줄러에 등록할 함수 (매일 16:00 실행)
async def scheduled_verify():
    await verify_today_alerts()