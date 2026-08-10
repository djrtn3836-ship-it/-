"""
report/telegram_sender.py - HTML 파싱 및 원시 전송 지원
"""
import os
import html
from telegram import Bot
from telegram.error import TelegramError
from dotenv import load_dotenv
from core.logger import setup_logger

logger = setup_logger("telegram")

class TelegramSender:
    def __init__(self):
        load_dotenv()
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.bot = Bot(token=self.token) if self.token else None
        
        if self.bot and self.chat_id:
            logger.info(f"✅ Telegram 봇 초기화 완료 (Chat ID: {self.chat_id})")
        else:
            logger.warning("❌ Telegram bot not configured")

    async def send(self, report: dict) -> bool:
        """기존 리포트 포맷 전송 (Markdown -> HTML 변환)"""
        if not self.bot or not self.chat_id:
            return False
        try:
            message = self._format_report_html(report)
            return await self.send_raw(message)
        except Exception as e:
            logger.error(f"❌ Telegram 전송 오류: {e}")
            return False

    async def send_raw(self, message: str) -> bool:
        """⚠️ Gemini 지적 반영: _send_raw 구현 완료 (HTML 모드, 길이 제한)"""
        if not self.bot or not self.chat_id:
            logger.warning("❌ Telegram bot 또는 chat_id 없음")
            return False
        
        # 1. 길이 제한 (4096자)
        if len(message) > 4000:
            message = message[:4000] + "\n\n...(메시지 길이 초과로 생략)"
        
        # 2. HTML 태그를 제외한 본문 내용 이스케이프 처리 (보안 및 파싱 오류 방지)
        # (이미 Markdown용 \n 등은 HTML에서 <br>로 변환하거나 유지)
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="HTML"
            )
            logger.info("✅ Telegram 메시지 전송 성공")
            return True
        except TelegramError as e:
            logger.error(f"❌ Telegram 전송 실패: {e}")
            return False

    def _format_report_html(self, report: dict) -> str:
        """HTML 기반 포맷팅 (특수문자 안전)"""
        ticker = html.escape(report.get("ticker", "N/A"))
        action = html.escape(report.get("action", "HOLD"))
        score = report.get("score", 0.0)
        confidence = report.get("confidence", 0.0)
        
        positives = report.get("positives", [])
        negatives = report.get("negatives", [])
        cf = report.get("counterfactuals", [])
        
        lines = []
        lines.append(f"<b>📊 {ticker} 분석 리포트</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🎯 <b>판단</b>: {action}")
        lines.append(f"📈 <b>신뢰도</b>: {confidence:.0%}")
        lines.append(f"📊 <b>점수</b>: {score:.0%}")
        lines.append("")
        lines.append("🔥 <b>WHY NOW?</b>")
        if positives:
            lines.extend([f"• {html.escape(p)}" for p in positives[:3]])
        else:
            lines.append("• 특이사항 없음")
        lines.append("")
        lines.append("❌ <b>WHY NOT?</b>")
        if negatives:
            lines.extend([f"• {html.escape(n)}" for n in negatives[:3]])
        else:
            lines.append("• 특이사항 없음")
        lines.append("")
        lines.append("💭 <b>Counterfactual 분석</b>")
        if cf and len(cf) > 0:
            for c in cf[:3]:
                lines.append(f"• {html.escape(c.get('scenario', ''))}: {html.escape(c.get('reasoning', [''])[0] if c.get('reasoning') else 'N/A')}")
        else:
            lines.append("• 분석 데이터 없음")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append("<i>Phase 1 Shadow Mode | 참고용</i>")
        
        return "\n".join(lines)