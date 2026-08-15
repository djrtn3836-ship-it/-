"""
report/telegram_sender.py - v5.9.0 (트레일링 스탑 업데이트 템플릿 추가)
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
            action = report.get("action")
            if action == "TRAILING_STOP_UPDATE":
                message = self._format_trailing_update(report)
            elif action == "EXIT":
                message = self._format_exit_signal(report)
            else:
                message = self._format_report_html(report)
            return await self.send_raw(message)
        except Exception as e:
            logger.error(f"❌ Telegram 전송 오류: {e}")
            return False

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

    # ============================================================
    # 기존 리포트 포맷 (변경 없음)
    # ============================================================
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

        if imbalance is not None and 0 <= imbalance <= 1:
            bar = "█" * int(imbalance * 10) + "░" * (10 - int(imbalance * 10))
            side = "매수" if imbalance > 0.55 else ("매도" if imbalance < 0.45 else "중립")
            lines.append(f"⚖️ <b>호가 불균형</b>: {side} 우세 ({imbalance:.1%}) {bar}")
            if pressure:
                lines.append(f"  • {html.escape(pressure)}")
            lines.append("")

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

        if positives:
            lines.append("✅ <b>매수 근거</b>")
            for p in positives[:3]:
                if p and isinstance(p, str):
                    lines.append(f"  • {html.escape(p)}")
            lines.append("")

        if negatives:
            lines.append("⚠️ <b>주의사항</b>")
            for n in negatives[:2]:
                if n and isinstance(n, str):
                    lines.append(f"  • {html.escape(n)}")
            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append("<i>Phase 1 Shadow Mode | 참고용</i>")
        lines.append(f"<i>🕒 {__import__('datetime').datetime.now().strftime('%H:%M:%S')} KST</i>")

        return "\n".join(lines)

    # ============================================================
    # 🔥 트레일링 스탑 업데이트 템플릿 (신규)
    # ============================================================
    def _format_trailing_update(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        price = data.get("price", 0.0)
        entry_price = data.get("entry_price", price)
        old_stop = data.get("old_stop", 0.0)
        new_stop = data.get("new_stop", 0.0)
        highest = data.get("highest_price")
        lowest = data.get("lowest_price")
        atr = data.get("atr", 0.0)

        if highest:
            direction = "📈 매수(Long)"
            high_low_text = f"📈 최고가: {highest:,.0f}원"
            profit_pct = ((price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
        else:
            direction = "📉 매도(Short)"
            high_low_text = f"📉 최저가: {lowest:,.0f}원"
            profit_pct = ((entry_price - price) / entry_price) * 100 if entry_price > 0 else 0

        lines = []
        lines.append("🔄 <b>[트레일링 스탑 업데이트]</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📌 <b>종목</b>: {ticker}")
        lines.append(f"📊 <b>포지션</b>: {direction}")
        lines.append(f"💰 <b>현재가</b>: {price:,.0f}원")
        lines.append(f"📈 <b>진입가</b>: {entry_price:,.0f}원")
        lines.append(f"📊 <b>평가 손익</b>: {profit_pct:+.1f}%")
        lines.append("")
        lines.append("🛡️ <b>손절가 변경</b>")
        lines.append(f"   • 🔻 <b>이전 손절</b>: {old_stop:,.0f}원")
        lines.append(f"   • 🟢 <b>신규 손절</b>: {new_stop:,.0f}원")
        lines.append(f"   • 📊 <b>상승 폭</b>: {new_stop - old_stop:+,.0f}원")
        lines.append("")
        lines.append("📈 <b>추가 정보</b>")
        lines.append(f"   • {high_low_text}")
        lines.append(f"   • 📊 ATR: {atr:,.0f}원")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append("<i>⚠️ 손절가가 상승하여 수익이 보호되었습니다.</i>")
        lines.append(f"<i>🕒 {__import__('datetime').datetime.now().strftime('%H:%M:%S')} KST</i>")
        return "\n".join(lines)

    # ============================================================
    # 🔥 청산 신호 템플릿 (신규)
    # ============================================================
    def _format_exit_signal(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        price = data.get("price", 0.0)
        entry_price = data.get("entry_price", price)
        stop_price = data.get("stop_price", price)
        highest = data.get("highest_price")
        lowest = data.get("lowest_price")

        if highest:
            direction = "📈 매수(Long)"
            profit_pct = ((price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
            high_low_text = f"📈 최고가: {highest:,.0f}원"
        else:
            direction = "📉 매도(Short)"
            profit_pct = ((entry_price - price) / entry_price) * 100 if entry_price > 0 else 0
            high_low_text = f"📉 최저가: {lowest:,.0f}원"

        lines = []
        lines.append("🔴 <b>[청산 신호] EXIT (트레일링 스탑 도달)</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📌 <b>종목</b>: {ticker}")
        lines.append(f"📊 <b>포지션</b>: {direction}")
        lines.append(f"💰 <b>청산가</b>: {price:,.0f}원")
        lines.append(f"📈 <b>진입가</b>: {entry_price:,.0f}원")
        lines.append(f"📊 <b>최종 손익</b>: {profit_pct:+.1f}%")
        lines.append("")
        lines.append("🛡️ <b>청산 사유</b>")
        lines.append(f"   • 🔻 트레일링 스탑 도달: {stop_price:,.0f}원")
        lines.append(f"   • {high_low_text}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append("<i>⚠️ 포지션이 청산되었습니다. (Shadow Mode - 알림 전용)</i>")
        lines.append(f"<i>🕒 {__import__('datetime').datetime.now().strftime('%H:%M:%S')} KST</i>")
        return "\n".join(lines)