"""
report/telegram_sender.py - v5.6.7 FINAL (전송 재시도 2회)
"""
import os
import html
import asyncio
from typing import Optional
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
        if not self.bot or not self.chat_id:
            return False
        try:
            message = self._format_report_html(report)
            return await self.send_raw(message)
        except Exception as e:
            logger.error(f"❌ Telegram 전송 오류: {e}")
            return False

    # ============================================================
    # 🔥 전송 재시도 (최대 2회, 지수 백오프)
    # ============================================================
    async def send_raw(self, message: str, max_retries: int = 2) -> bool:
        if not self.bot or not self.chat_id:
            return False

        if len(message) > 4000:
            message = message[:3950] + "\n\n... (메시지 길이 초과)"

        for attempt in range(max_retries + 1):
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode="HTML"
                )
                if attempt > 0:
                    logger.info(f"✅ Telegram 재전송 성공 ({attempt}회차)")
                else:
                    logger.info("✅ Telegram 메시지 전송 성공")
                return True
            except TelegramError as e:
                if attempt < max_retries:
                    delay = 2 ** attempt
                    logger.warning(f"⚠️ Telegram 전송 실패 ({attempt+1}/{max_retries+1}): {e} → {delay}초 후 재시도")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"❌ Telegram 전송 최종 실패: {e}")
                    return False
            except Exception as e:
                logger.error(f"❌ Telegram 전송 예외: {e}")
                return False
        return False

    def _format_report_html(self, report: dict) -> str:
        ticker = html.escape(str(report.get("ticker", "N/A")))
        name = html.escape(str(report.get("name", ticker)))
        action = html.escape(str(report.get("action", "HOLD")))
        score = report.get("score", 0.5)
        confidence = report.get("confidence", 0.5)
        price = report.get("price", 0.0)
        momentum = report.get("momentum", 0.0)
        positives = report.get("positives", [])
        negatives = report.get("negatives", [])
        entry_price = report.get("entry_price", price)
        atr = report.get("atr", 0.0)
        imbalance = report.get("imbalance")
        pressure = report.get("pressure", "")
        support = report.get("support_level")
        resistance = report.get("resistance_level")

        is_emergency = abs(momentum) > 0.05
        if is_emergency:
            header_emoji = "🚨"
            header_title = "긴급 알림 (Emergency)"
        elif action == "BUY" and confidence > 0.7:
            header_emoji = "📈"
            header_title = "강력 매수 시그널 (Strong Buy)"
        elif action == "SELL" and confidence > 0.7:
            header_emoji = "📉"
            header_title = "강력 매도 시그널 (Strong Sell)"
        else:
            header_emoji = "📊"
            header_title = "분석 리포트 (Analysis)"

        action_emoji = "🟢" if action == "BUY" else ("🔴" if action == "SELL" else "⚪")

        lines = []
        lines.append(f"<b>{header_emoji} {header_title}</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<b>{action_emoji} [{action}] {name} ({ticker})</b>")
        lines.append("")

        price_str = f"{price:,.0f} 원" if price > 0 else "데이터 없음"
        change_str = f"{momentum:+.2%}" if momentum != 0 else "0.00%"
        lines.append(f"💰 <b>현재가</b>: <code>{price_str}</code>  |  <b>변동률</b>: <code>{change_str}</code>")

        if entry_price > 0:
            lines.append(f"📌 <b>진입가</b>: <code>{entry_price:,.0f} 원</code>")

        conf_bar = "█" * int(confidence * 10) + "░" * (10 - int(confidence * 10))
        score_bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
        lines.append(f"📊 <b>신뢰도</b>: {confidence:.1%} {conf_bar}")
        lines.append(f"📊 <b>점수</b>: {score:.1%} {score_bar}")
        lines.append("")

        # 손절/익절 (ATR 기반)
        if atr > 0 and entry_price > 0:
            sl1 = entry_price - atr
            sl2 = entry_price - atr * 1.5
            tp1 = entry_price + atr * 2
            tp2 = entry_price + atr * 3
            rr = (tp1 - entry_price) / (entry_price - sl1) if (entry_price - sl1) > 0 else 0

            lines.append("━━━━━━━━━━━━━━━━━━━━━")
            lines.append("🎯 <b>손절·익절 가격대 (ATR 기반)</b>")
            lines.append(f"🛑 <b>1차 손절</b>: <code>{sl1:,.0f} 원</code> (ATR × 1.0)")
            lines.append(f"🛑 <b>2차 손절</b>: <code>{sl2:,.0f} 원</code> (ATR × 1.5)")
            lines.append("")
            lines.append(f"🎯 <b>1차 익절</b>: <code>{tp1:,.0f} 원</code> (ATR × 2.0)")
            lines.append(f"🎯 <b>2차 익절</b>: <code>{tp2:,.0f} 원</code> (ATR × 3.0)")
            lines.append("")
            lines.append(f"⚖️ <b>위험-보상 비율</b>: <code>{rr:.1f}:1</code>")
            lines.append("━━━━━━━━━━━━━━━━━━━━━")
            lines.append("")

        # 기술적 인사이트
        if support or resistance:
            insight = []
            if support and price > 0:
                insight.append(f"📈 지지선 <b>{support:,.0f}</b>원 상향 이탈")
            if resistance and price > 0:
                insight.append(f"📉 저항선 <b>{resistance:,.0f}</b>원 하향 이탈")
            if insight:
                lines.append("🔍 <b>기술적 인사이트</b>")
                for txt in insight:
                    lines.append(f"  • {txt}")
                lines.append("")

        # 호가 불균형
        if imbalance is not None and 0 <= imbalance <= 1:
            bar = "█" * int(imbalance * 10) + "░" * (10 - int(imbalance * 10))
            side = "매수" if imbalance > 0.55 else ("매도" if imbalance < 0.45 else "중립")
            lines.append(f"⚖️ <b>호가 불균형</b>: {side} 우세 ({imbalance:.1%}) {bar}")
            if pressure:
                lines.append(f"  • {html.escape(pressure)}")
            lines.append("")

        # 포지션 비중
        if action in ["BUY", "SELL"] and confidence > 0.5:
            kelly_ratio = min(15.0, max(2.0, (confidence * 20) - (abs(momentum) * 50)))
            if momentum > 0 and action == "BUY":
                rec = min(15.0, kelly_ratio * 1.2)
            elif momentum < 0 and action == "SELL":
                rec = min(15.0, kelly_ratio * 0.8)
            else:
                rec = min(15.0, kelly_ratio)
            lines.append(f"🎯 <b>권장 포지션</b>: <code>{rec:.1f}%</code> (Kelly 변형)")
            lines.append("")

        # 매수 근거
        if positives:
            lines.append("✅ <b>매수 근거</b>")
            for p in positives[:3]:
                if p and isinstance(p, str):
                    lines.append(f"  • {html.escape(p)}")
            lines.append("")

        # 주의사항
        if negatives:
            lines.append("⚠️ <b>주의사항</b>")
            for n in negatives[:2]:
                if n and isinstance(n, str):
                    lines.append(f"  • {html.escape(n)}")
            lines.append("")

        # 푸터
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append("<i>Phase 1 Shadow Mode | 참고용</i>")
        lines.append(f"<i>🕒 {__import__('datetime').datetime.now().strftime('%H:%M:%S')} KST</i>")

        return "\n".join(lines)