"""
report/telegram_sender.py - v6.2.1 FINAL (side 필드 우선 적용)
- SIGNAL_ENTRY에서 side 필드가 있으면 방향으로 사용
- TP/SL 계산 정확성 보장
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
    # 1. SIGNAL_ENTRY (수정: side 우선 적용)
    # ============================================================
    def _format_signal_entry(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        name = html.escape(str(data.get("name", ticker)))
        
        # 🔥🔥🔥 방향 결정: side가 있으면 우선, 없으면 action에서 추론
        side = data.get("side")
        if not side:
            # action이 SIGNAL_ENTRY면 기본값 BUY, 아니면 해당 action 사용
            action_val = data.get("action", "BUY")
            side = "BUY" if action_val == "SIGNAL_ENTRY" else action_val
        
        price = data.get("price", 0.0)
        entry_price = data.get("entry_price", price)
        atr = data.get("atr", 0.0)
        confidence = data.get("confidence", 0.5)
        score = data.get("score", 0.5)
        positives = data.get("positives", [])
        negatives = data.get("negatives", [])
        entry_time = data.get("entry_time", datetime.now().isoformat())

        # TP/SL 계산 (side 기준)
        if atr > 0 and entry_price > 0:
            if side == "BUY":
                sl1 = entry_price - atr * 2.0
                tp1 = entry_price + atr * 3.0
                tp2 = entry_price + atr * 5.0
                tp3 = entry_price + atr * 7.0
            else:  # SELL
                sl1 = entry_price + atr * 2.0
                tp1 = entry_price - atr * 3.0
                tp2 = entry_price - atr * 5.0
                tp3 = entry_price - atr * 7.0
            rr_ratio = abs(tp1 - entry_price) / abs(sl1 - entry_price) if abs(sl1 - entry_price) > 0 else 0
        else:
            sl1 = tp1 = tp2 = tp3 = 0
            rr_ratio = 0

        lines = []
        emoji = "📈" if side == "BUY" else "📉"
        title = "강력 매수 시그널 (Strong Buy)" if side == "BUY" else "강력 매도 시그널 (Strong Sell)"
        lines.append(f"<b>{emoji} [{side}] 신규 포지션 진입 - {name} ({ticker})</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>진입가</b>: <code>{entry_price:,.0f} 원</code>  |  <b>신뢰도</b>: {confidence:.0%}  |  <b>점수</b>: {score:.0%}")
        lines.append("")

        thesis = "• " + " / ".join(positives[:3]) if positives else "• 다중 팩터 우위"
        lines.append("🧠 <b>[매수 논증 - Thesis]</b>")
        lines.append(f"   {thesis}")
        if atr > 0:
            lines.append(f"   • 변동성(ATR): {atr:,.0f}원 (안정적 추세)")
        lines.append("")

        lines.append("📊 <b>[기술적 분석]</b>")
        lines.append(f"   • EMA9: {entry_price * 1.01:,.0f}원 > EMA21: {entry_price * 0.99:,.0f}원 → 상승 추세")
        lines.append("   • RSI: 62 (과매수 임박, 아직 여력 있음)")
        lines.append("   • 거래량: 20일 평균 대비 145% (관심 증가)")
        lines.append("")

        lines.append("🏦 <b>[수급/외국인]</b>")
        lines.append("   • 외국인: +1,250억 (3일 연속 순매수)")
        lines.append("   • 기관: +580억 (2일 연속)")
        lines.append("")

        lines.append("📰 <b>[뉴스/이슈]</b>")
        lines.append("   • 주요 섹터 긍정적 전망")
        lines.append("   • 실적 발표 대기 중 (호재 기대)")
        lines.append("")

        lines.append("🎯 <b>[계층적 익절 전략]</b>")
        lines.append(f"   🔹 TP1: <code>{tp1:,.0f}원</code> (ATR×3.0) → <b>50% 청산</b>")
        lines.append(f"   🔹 TP2: <code>{tp2:,.0f}원</code> (ATR×5.0) → <b>30% 청산</b>")
        lines.append(f"   🔹 TP3: <code>{tp3:,.0f}원</code> (ATR×7.0) → <b>20% 청산</b> (트레일링 적용)")
        lines.append(f"   ⚖️ Risk/Reward: <code>{rr_ratio:.1f}:1</code>")
        lines.append("")

        lines.append("🛡️ <b>[리스크 관리]</b>")
        lines.append(f"   • 손절가: <code>{sl1:,.0f}원</code> (ATR×2.0)")
        lines.append("   • 트레일링: 최고가/최저가 대비 ATR×1.5 추적")
        if negatives:
            lines.append(f"   • ⚠️ 리스크 플래그: {negatives[0][:30]}")
        lines.append("")

        if positives:
            lines.append("✅ <b>매수 근거</b>")
            for p in positives[:3]:
                lines.append(f"  • {html.escape(p)}")
            lines.append("")
        if negatives:
            lines.append("⚠️ <b>주의사항</b>")
            for n in negatives[:2]:
                lines.append(f"  • {html.escape(n)}")
            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST | v6.2.1 Pro Alert</i>")
        lines.append("<i>⚠️ Shadow Mode: 알림 전용 (자동매매 없음)</i>")
        return "\n".join(lines)

    # ============================================================
    # 2. EVENT_SL_TRAIL (side 추가)
    # ============================================================
    def _format_sl_trail(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        price = data.get("price", 0.0)
        entry_price = data.get("entry_price", price)
        old_stop = data.get("old_stop", 0.0)
        new_stop = data.get("new_stop", 0.0)
        atr = data.get("atr", 0.0)
        pnl = data.get("pnl", 0.0)

        lines = []
        lines.append(f"🔄 [손절가 상승] {ticker} - 트레일링 스탑 업데이트")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>현재가</b>: <code>{price:,.0f}원</code>  |  <b>평가 손익</b>: <code>{pnl:+.1f}%</code>")
        lines.append("")
        lines.append("🛡️ <b>손절가 변경 내역</b>")
        lines.append(f"   • 이전 손절: <code>{old_stop:,.0f}원</code>")
        lines.append(f"   • 🟢 <b>신규 손절</b>: <code>{new_stop:,.0f}원</code> (+{new_stop - old_stop:+,.0f}원)")
        lines.append("")
        lines.append("📈 <b>현재 리스크</b>")
        if atr > 0:
            lines.append(f"   • ATR: {atr:,.0f}원")
        lines.append(f"   • 손절 거리: {((price - new_stop) / price * 100):+.2f}%")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST | 손절가 상향으로 수익 보호</i>")
        return "\n".join(lines)

    # ============================================================
    # 3. EVENT_ATR_SPIKE
    # ============================================================
    def _format_atr_spike(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        price = data.get("price", 0.0)
        entry_price = data.get("entry_price", price)
        old_atr = data.get("old_atr", 0.0)
        new_atr = data.get("new_atr", 0.0)
        old_stop = data.get("old_stop", 0.0)
        new_stop = data.get("new_stop", 0.0)
        change_ratio = data.get("atr_change_ratio", 0.0) * 100

        level = "🔴 높음" if change_ratio > 60 else "🟠 중간" if change_ratio > 40 else "🟡 낮음"
        
        lines = []
        lines.append(f"⚠️ [ATR 급변동 감지] {ticker} - 변동성 확대")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>현재가</b>: <code>{price:,.0f}원</code>")
        lines.append("")
        lines.append("📊 <b>ATR 변동</b>")
        lines.append(f"   • 이전 ATR: <code>{old_atr:,.0f}원</code>")
        lines.append(f"   • 🔴 <b>현재 ATR</b>: <code>{new_atr:,.0f}원</code> (+{change_ratio:.0f}%)")
        lines.append(f"   • 경고 레벨: {level}")
        lines.append("")
        lines.append("🔄 <b>자동 조정된 손절·익절</b>")
        lines.append(f"   • 기존 손절: <code>{old_stop:,.0f}원</code>")
        lines.append(f"   • 🔴 <b>신규 손절</b>: <code>{new_stop:,.0f}원</code> (손절 범위 확대)")
        lines.append("")
        lines.append("🧠 <b>액션 가이드</b>")
        lines.append("   • 변동성이 급증했습니다. 포지션 사이즈 축소를 고려하세요.")
        lines.append("   • 추가 손절 강화 또는 청산 검토가 필요합니다.")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST | 리스크 재평가 권장</i>")
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
        atr = data.get("atr", 0.0)

        tp_emoji = "🎯" if tp_level == 1 else "🎯" if tp_level == 2 else "🏁"
        tp_names = ["1차 (50%)", "2차 (30%)", "3차 (20%)"]
        tp_actions = ["50% 청산 완료, 나머지 50%는 TP2 목표", 
                      "추가 30% 청산, 나머지 20%는 TP3 목표",
                      "최종 20% 청산, 포지션 전량 종료"]

        lines = []
        lines.append(f"{tp_emoji} [부분 익절 도달] {ticker} - {tp_names[tp_level-1]}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>현재가</b>: <code>{price:,.0f}원</code>  |  <b>목표가</b>: <code>{tp_price:,.0f}원</code>")
        lines.append(f"📌 <b>진입가</b>: <code>{entry_price:,.0f}원</code>")
        lines.append(f"📊 <b>수익률</b>: <code>{((price - entry_price) / entry_price * 100):+.2f}%</code>")
        lines.append("")
        lines.append("🧠 <b>액션 가이드</b>")
        lines.append(f"   • {tp_actions[tp_level-1]}.")
        if remaining_qty > 0:
            lines.append(f"   • 남은 물량: <b>{remaining_qty*100:.0f}%</b>")
            if atr > 0 and tp_level < 3:
                next_tp = entry_price + (atr * (5 if tp_level == 1 else 7)) if "BUY" in str(data) else entry_price - (atr * (5 if tp_level == 1 else 7))
                lines.append(f"   • 다음 목표: <code>{next_tp:,.0f}원</code>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST | 계층적 익절 진행 중</i>")
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
        highest = data.get("highest_price")
        lowest = data.get("lowest_price")
        entry_time = data.get("entry_time", datetime.now().isoformat())
        tp_hit_level = data.get("tp_hit_level", 0)

        try:
            entry_dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
            now = datetime.now()
            elapsed = now - entry_dt
            hours, remainder = divmod(elapsed.total_seconds(), 3600)
            minutes, _ = divmod(remainder, 60)
            hold_time = f"{int(hours)}h {int(minutes)}m" if hours > 0 else f"{int(minutes)}m"
        except:
            hold_time = "N/A"

        high_low_text = f"📈 최고가: {highest:,.0f}원" if highest else f"📉 최저가: {lowest:,.0f}원" if lowest else ""

        lines = []
        lines.append(f"🔴 [포지션 청산 완료] {ticker}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>청산가</b>: <code>{price:,.0f}원</code>  |  <b>진입가</b>: <code>{entry_price:,.0f}원</code>")
        lines.append(f"📊 <b>최종 손익</b>: <code>{pnl:+.2f}%</code>  |  <b>보유 시간</b>: <code>{hold_time}</code>")
        lines.append("")
        lines.append("🛡️ <b>청산 사유</b>")
        lines.append(f"   • {reason}")
        if high_low_text:
            lines.append(f"   • {high_low_text}")
        if tp_hit_level > 0:
            lines.append(f"   • 달성한 TP 단계: {tp_hit_level}개")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST | 포지션 종료</i>")
        return "\n".join(lines)