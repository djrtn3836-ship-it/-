"""
report/telegram_sender.py - v7.2.1 FINAL (합의 엔진 + 동적 TP)
- 4대 독립 개체 투표 결과, 합의 점수, 판단 근거 표시
- ATR 급변동 시 동적 TP 조정 정보 포함
"""

import os
import html
import asyncio
from typing import Optional, Dict
from datetime import datetime, timedelta
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
            if action == "SIGNAL_ENTRY":
                message = self._format_signal_entry(report)
            elif action == "EVENT_SL_TRAIL":
                message = self._format_sl_trail(report)
            elif action == "EVENT_ATR_SPIKE":
                message = self._format_atr_spike(report)
            elif action == "EVENT_TP_HIT":
                message = self._format_tp_hit(report)
            elif action == "EVENT_EXIT":
                message = self._format_exit(report)
            elif action == "EVENT_LIFECYCLE_ADVICE":
                message = self._format_lifecycle_advice(report)
            else:
                return True
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
    # 1. SIGNAL_ENTRY (신규 진입)
    # ============================================================
    def _format_signal_entry(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        name = html.escape(str(data.get("name", ticker)))
        side = data.get("side", "BUY")
        price = data.get("price", 0.0)
        entry_price = data.get("entry_price", price)
        atr = data.get("atr", 0.0)
        confidence = data.get("confidence", 0.5)
        score = data.get("score", 0.5)
        positives = data.get("positives", [])
        negatives = data.get("negatives", [])
        max_hold_hours = data.get("max_hold_hours", 2)

        if atr > 0 and entry_price > 0:
            sl1 = entry_price - atr * 2.0 if side == "BUY" else entry_price + atr * 2.0
            tp1 = entry_price + atr * 3.0 if side == "BUY" else entry_price - atr * 3.0
            tp2 = entry_price + atr * 5.0 if side == "BUY" else entry_price - atr * 5.0
            tp3 = entry_price + atr * 7.0 if side == "BUY" else entry_price - atr * 7.0
            rr_ratio = abs(tp1 - entry_price) / abs(sl1 - entry_price) if abs(sl1 - entry_price) > 0 else 0
        else:
            sl1 = tp1 = tp2 = tp3 = 0
            rr_ratio = 0

        lines = []
        emoji = "📈" if side == "BUY" else "📉"
        title = "강력 매수 시그널" if side == "BUY" else "강력 매도 시그널"
        lines.append(f"<b>{emoji} [{side}] {title} - {name} ({ticker})</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>진입가</b>: <code>{entry_price:,.0f} 원</code>")
        lines.append(f"📊 <b>신뢰도</b>: {confidence:.0%}  |  <b>점수</b>: {score:.0%}")
        lines.append(f"⏰ <b>예상 최대 보유 시간</b>: <code>{max_hold_hours}시간</code>")
        lines.append("")

        thesis = "• " + " / ".join(positives[:3]) if positives else "• 다중 팩터 우위"
        lines.append("🧠 <b>[매수 논증]</b>")
        lines.append(f"   {thesis}")
        if atr > 0:
            lines.append(f"   • 변동성(ATR): {atr:,.0f}원")
        lines.append("")

        lines.append("🎯 <b>[계층적 익절 전략]</b>")
        lines.append(f"   🔹 TP1: <code>{tp1:,.0f}원</code> (ATR×3.0) → <b>50% 청산</b>")
        lines.append(f"   🔹 TP2: <code>{tp2:,.0f}원</code> (ATR×5.0) → <b>30% 청산</b>")
        lines.append(f"   🔹 TP3: <code>{tp3:,.0f}원</code> (ATR×7.0) → <b>20% 청산</b>")
        lines.append(f"   ⚖️ Risk/Reward: <code>{rr_ratio:.1f}:1</code>")
        lines.append("")

        lines.append("🛡️ <b>[리스크 관리]</b>")
        lines.append(f"   • 손절가: <code>{sl1:,.0f}원</code> (ATR×2.0)")
        lines.append(f"   • 트레일링: 최고가/최저가 대비 ATR×1.5 추적")
        lines.append(f"   • ⏰ 시간 조건: {max_hold_hours}시간 초과 시 재평가")
        lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST | v7.2.1 Pro Alert</i>")
        lines.append("<i>⚠️ Shadow Mode: 알림 전용</i>")
        return "\n".join(lines)

    # ============================================================
    # 2. EVENT_SL_TRAIL
    # ============================================================
    def _format_sl_trail(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        price = data.get("price", 0.0)
        entry_price = data.get("entry_price", price)
        old_stop = data.get("old_stop", 0.0)
        new_stop = data.get("new_stop", 0.0)
        atr = data.get("atr", 0.0)
        pnl = data.get("pnl", 0.0)
        advice = data.get("consensus")

        lines = []
        lines.append(f"🔄 [손절가 상승] {ticker} - 트레일링 스탑 업데이트")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>현재가</b>: <code>{price:,.0f}원</code>  |  <b>평가 손익</b>: <code>{pnl:+.1f}%</code>")
        lines.append("")
        lines.append("🛡️ <b>손절가 변경 내역</b>")
        lines.append(f"   • 이전 손절: <code>{old_stop:,.0f}원</code>")
        lines.append(f"   • 🟢 <b>신규 손절</b>: <code>{new_stop:,.0f}원</code> (+{new_stop - old_stop:+,.0f}원)")
        lines.append("")
        if advice:
            rec = advice.get("recommendation")
            reason = advice.get("summary", "")
            if rec == "EXIT":
                emoji = "🔴"
                rec_text = "청산 권고"
            elif rec == "PARTIAL_EXIT":
                emoji = "🟡"
                rec_text = "부분 익절"
            else:
                emoji = "🟢"
                rec_text = "홀드 유지"
            lines.append(f"{emoji} <b>합의 엔진 권고</b>: {rec_text}")
            if reason:
                lines.append(f"   • {reason}")
            lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST</i>")
        return "\n".join(lines)

    # ============================================================
    # 3. EVENT_ATR_SPIKE (동적 TP 포함)
    # ============================================================
    def _format_atr_spike(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        price = data.get("price", 0.0)
        entry_price = data.get("entry_price", 0.0)
        old_atr = data.get("old_atr", 0.0)
        new_atr = data.get("new_atr", 0.0)
        old_stop = data.get("old_stop", 0.0)
        new_stop = data.get("new_stop", 0.0)
        change_ratio = data.get("atr_change_ratio", 0.0) * 100
        tp_adjusted = data.get("tp_adjusted", False)
        tp2 = data.get("tp2_price", 0.0)
        tp3 = data.get("tp3_price", 0.0)

        level = "🔴 높음" if change_ratio > 60 else "🟠 중간" if change_ratio > 40 else "🟡 낮음"
        
        lines = []
        lines.append(f"⚠️ [ATR 급변동 감지] {ticker} - 변동성 확대")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>현재가</b>: <code>{price:,.0f}원</code>")
        lines.append("")
        lines.append("📊 <b>ATR 변동</b>")
        lines.append(f"   • 이전 ATR: <code>{old_atr:,.0f}원</code>")
        lines.append(f"   • 🔴 <b>현재 ATR</b>: <code>{new_atr:,.0f}원</code> (+{change_ratio:.0f}%)")
        lines.append("")
        lines.append("🔄 <b>자동 조정된 손절</b>")
        lines.append(f"   • 기존 손절: <code>{old_stop:,.0f}원</code>")
        lines.append(f"   • 🔴 <b>신규 손절</b>: <code>{new_stop:,.0f}원</code>")
        
        if tp_adjusted and tp2 > 0 and tp3 > 0:
            lines.append("")
            lines.append("🎯 <b>동적 익절가 조정</b>")
            lines.append(f"   • 🔹 TP2: <code>{tp2:,.0f}원</code> (ATR×5.0 적용)")
            lines.append(f"   • 🔹 TP3: <code>{tp3:,.0f}원</code> (ATR×7.0 적용)")
        lines.append("")
        lines.append("🧠 <b>액션 가이드</b>")
        lines.append("   • 변동성 급증 → 포지션 사이즈 축소 고려")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST | v7.2.1</i>")
        return "\n".join(lines)

    # ============================================================
    # 4. EVENT_TP_HIT
    # ============================================================
    def _format_tp_hit(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        tp_level = data.get("tp_level", 1)
        tp_price = data.get("tp_price", 0.0)
        price = data.get("price", 0.0)
        entry_price = data.get("entry_price", price)
        remaining_qty = data.get("remaining_qty", 1.0)
        rec_reason = data.get("recommendation_reason", "")

        tp_names = ["1차 (50%)", "2차 (30%)", "3차 (20%)"]
        tp_emoji = "🎯" if tp_level == 1 else "🎯" if tp_level == 2 else "🏁"

        lines = []
        lines.append(f"{tp_emoji} [부분 익절 도달] {ticker} - {tp_names[tp_level-1]}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>현재가</b>: <code>{price:,.0f}원</code>  |  <b>목표가</b>: <code>{tp_price:,.0f}원</code>")
        lines.append(f"📊 <b>수익률</b>: <code>{((price - entry_price) / entry_price * 100):+.2f}%</code>")
        lines.append(f"📌 <b>남은 물량</b>: <code>{remaining_qty*100:.0f}%</code>")
        lines.append("")
        lines.append("🧠 <b>액션 가이드</b>")
        lines.append(f"   • {rec_reason}")
        if remaining_qty > 0:
            lines.append(f"   • 남은 물량은 트레일링 스탑으로 관리")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST</i>")
        return "\n".join(lines)

    # ============================================================
    # 5. EVENT_EXIT
    # ============================================================
    def _format_exit(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        price = data.get("price", 0.0)
        entry_price = data.get("entry_price", price)
        pnl = data.get("pnl", 0.0)
        reason = data.get("reason", "알 수 없음")
        hold_time = self._calc_hold_time(data.get("entry_time"))

        lines = []
        lines.append(f"🔴 [포지션 청산 완료] {ticker}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>청산가</b>: <code>{price:,.0f}원</code>  |  <b>진입가</b>: <code>{entry_price:,.0f}원</code>")
        lines.append(f"📊 <b>최종 손익</b>: <code>{pnl:+.2f}%</code>  |  <b>보유 시간</b>: <code>{hold_time}</code>")
        lines.append("")
        lines.append("🛡️ <b>청산 사유</b>")
        lines.append(f"   • {reason}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST</i>")
        return "\n".join(lines)

    # ============================================================
    # 6. EVENT_LIFECYCLE_ADVICE (합의 엔진 결과)
    # ============================================================
    def _format_lifecycle_advice(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        price = data.get("price", 0.0)
        entry_price = data.get("entry_price", price)
        pnl = data.get("pnl", 0.0)
        advice = data.get("advice", {})
        
        action_label = advice.get("action_label", "관망")
        consensus_score = advice.get("consensus_score", 0.0)
        votes = advice.get("votes", {})
        reasons = advice.get("reasons", [])
        summary = advice.get("summary", "")
        
        emoji = "🔴" if "청산" in action_label else "🟡" if "관망" in action_label else "🟢"
        
        vote_str = " | ".join([
            f"기술:{votes.get('technical', 0):+.2f}",
            f"리스크:{votes.get('risk', 0):+.2f}",
            f"시간:{votes.get('time_value', 0):+.2f}",
            f"수급:{votes.get('micro', 0):+.2f}"
        ])
        
        lines = []
        lines.append(f"{emoji} [🧠 합의 엔진] {ticker} - {action_label}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>현재가</b>: <code>{price:,.0f}원</code>")
        lines.append(f"📊 <b>평가 손익</b>: <code>{pnl:+.1f}%</code>  |  <b>진입가</b>: <code>{entry_price:,.0f}원</code>")
        lines.append("")
        lines.append("📋 <b>4대 개체 투표 결과</b>")
        lines.append(f"   {vote_str}")
        lines.append(f"   • 합의 점수: <code>{consensus_score:.2f}</code>")
        lines.append("")
        lines.append("🧠 <b>판단 근거</b>")
        for r in reasons[:4]:
            lines.append(f"   • {r}")
        if summary and not reasons:
            lines.append(f"   • {summary}")
        lines.append("")
        lines.append(f"🎯 <b>최종 권고</b>: {action_label}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST | v7.2.1 Consensus (데이터 기반)</i>")
        return "\n".join(lines)

    # ============================================================
    # 보유 시간 계산
    # ============================================================
    def _calc_hold_time(self, entry_time_str: str) -> str:
        if not entry_time_str:
            return "N/A"
        try:
            entry_dt = datetime.fromisoformat(entry_time_str.replace('Z', '+00:00'))
            now = datetime.now()
            elapsed = now - entry_dt
            hours, remainder = divmod(elapsed.total_seconds(), 3600)
            minutes, _ = divmod(remainder, 60)
            return f"{int(hours)}h {int(minutes)}m" if hours > 0 else f"{int(minutes)}m"
        except:
            return "N/A"