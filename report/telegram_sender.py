"""
report/telegram_sender.py - v7.3.1 (청크 전송 간격 추가)
- send_raw()에서 청크 간 0.1초 sleep 추가 (Telegram rate limit 방어)
- HTML 태그 분할 기본 로직 유지
"""

import asyncio
import html
import os
from datetime import datetime

from dotenv import load_dotenv
from telegram import Bot
from telegram.error import NetworkError, TelegramError, TimedOut

from core.debug_tower import debug_tower
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
            debug_tower.log("SYSTEM", "TELEGRAM_INIT", {"chat_id": self.chat_id})
        else:
            logger.warning("❌ Telegram bot not configured")

    async def send(self, report: dict) -> bool:
        if not self.bot or not self.chat_id:
            return False

        action = report.get("action")
        ticker = report.get("ticker", "UNKNOWN")
        debug_tower.log(ticker, "TELEGRAM_SEND_START", {"action": action})

        if action in ("ERROR", "IGNORE"):
            debug_tower.log(ticker, "TELEGRAM_SKIP", {"action": action})
            return True

        try:
            if action == "SIGNAL_ENTRY" or action in ("BUY", "SELL"):
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
                logger.warning(f"⚠️ 알 수 없는 action 타입 수신: {action!r}")
                debug_tower.log(ticker, "TELEGRAM_UNKNOWN_ACTION", {"action": action})
                return True

            result = await self.send_raw(message)
            if result:
                debug_tower.log(ticker, "TELEGRAM_SEND_SUCCESS", {"action": action})
            else:
                debug_tower.log(ticker, "TELEGRAM_SEND_FAIL", {"action": action})
            return result
        except Exception as e:
            logger.error(f"❌ Telegram 전송 오류: {e}")
            debug_tower.capture_snapshot(ticker, e, f"TELEGRAM_{action}")
            return False

    # ============================================================
    # 🔥 P1-5: 청크 간 sleep 추가
    # ============================================================
    async def send_raw(self, message: str, max_retries: int = 4) -> bool:
        if not self.bot or not self.chat_id:
            return False

        chunks = self._split_message(message)

        for i, chunk in enumerate(chunks, 1):
            success = False
            last_error = None

            for attempt in range(max_retries + 1):
                try:
                    delay = min(2 ** (attempt + 1), 60)
                    if attempt > 0:
                        logger.info(f"⏳ Telegram 재시도 {attempt}/{max_retries} ({delay}초 대기)...")
                        await asyncio.sleep(delay)

                    await asyncio.wait_for(
                        self.bot.send_message(chat_id=self.chat_id, text=chunk, parse_mode="HTML"), timeout=30.0
                    )
                    success = True
                    if len(chunks) > 1:
                        logger.info(f"✅ Telegram 청크 {i}/{len(chunks)} 전송 성공")
                    else:
                        logger.info("✅ Telegram 메시지 전송 성공")
                    break

                except TimeoutError:
                    last_error = "Timeout (30s)"
                    logger.warning(f"⚠️ Telegram 타임아웃 (시도 {attempt+1}/{max_retries+1})")
                    continue
                except (TimedOut, NetworkError) as e:
                    last_error = str(e)
                    logger.warning(f"⚠️ Telegram 네트워크 오류: {e} (시도 {attempt+1}/{max_retries+1})")
                    continue
                except TelegramError as e:
                    logger.error(f"❌ Telegram API 오류: {e}")
                    debug_tower.capture_snapshot("SYSTEM", e, "TELEGRAM_API")
                    return False
                except Exception as e:
                    logger.error(f"❌ Telegram 전송 예외: {e}")
                    debug_tower.capture_snapshot("SYSTEM", e, "TELEGRAM_EXCEPTION")
                    return False

            if not success:
                logger.error(f"❌ Telegram 청크 {i} 전송 최종 실패 (마지막 오류: {last_error})")
                debug_tower.log("SYSTEM", "TELEGRAM_CHUNK_FAIL", {"chunk": i, "error": last_error})
                return False

            # 🔥 P1-5: 청크 간 0.1초 대기 (Telegram rate limit 방어)
            if i < len(chunks):
                await asyncio.sleep(0.1)

        return True

    def _split_message(self, message: str) -> list[str]:
        max_len = 3900
        if len(message) <= max_len:
            return [message]

        chunks = []
        lines = message.split("\n")
        current_chunk = ""

        for line in lines:
            if len(current_chunk) + len(line) + 1 <= max_len:
                current_chunk += line + "\n"
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = line + "\n"

        if current_chunk:
            chunks.append(current_chunk.strip())

        final_chunks = []
        for chunk in chunks:
            if len(chunk) <= max_len:
                final_chunks.append(chunk)
            else:
                for i in range(0, len(chunk), max_len):
                    final_chunks.append(chunk[i : i + max_len])

        logger.debug(f"📨 메시지 분할: {len(final_chunks)}개 청크")
        return final_chunks

    # ============================================================
    # 기존 포맷터 (v7.2.8 그대로 유지, 변경 없음)
    # ============================================================
    _ACTION_STYLE = {
        "EXIT": ("🔴", "지금 즉시 전량 매도"),
        "PARTIAL_EXIT": ("🟡", "지금 즉시 부분 매도"),
        "REDUCE": ("🟠", "포지션 축소 검토 (매도 아님)"),
        "HOLD": ("🟢", "보유 유지 (행동 불필요)"),
        "WATCH": ("⚪", "관찰만 (행동 불필요)"),
        "EXECUTED": ("✅", "자동 실행 완료"),
    }

    def _action_banner(self, action: str, price: float = 0.0, note: str = "") -> list:
        emoji, label = self._ACTION_STYLE.get(action, self._ACTION_STYLE["WATCH"])
        lines = [f"{emoji} <b>지금 할 일: {label}</b>"]
        if price > 0:
            lines.append(f"   → 기준가: <code>{price:,.0f}원</code>")
        if note:
            lines.append(f"   → {note}")
        lines.append("")
        return lines

    def _infer_advice_action(self, advice: dict | None) -> str:
        if not advice:
            return "HOLD"
        rec = (advice.get("recommendation") or advice.get("action") or "").upper()
        if rec in ("EXIT", "SELL", "CLOSE"):
            return "EXIT"
        if rec in ("PARTIAL_EXIT", "PARTIAL_SELL"):
            return "PARTIAL_EXIT"
        if rec in ("REDUCE",):
            return "REDUCE"
        label = str(advice.get("action_label", ""))
        if "청산" in label or "매도" in label:
            return "EXIT" if "전량" in label or "전체" in label else "PARTIAL_EXIT"
        if "축소" in label:
            return "REDUCE"
        return "HOLD"

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
        if side == "BUY":
            lines.append("🧠 <b>[매수 논증]</b>")
        else:
            lines.append("🧠 <b>[매도 논증]</b>")
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
        lines.append("   • 트레일링: 최고가/최저가 대비 ATR×1.5 추적")
        lines.append(f"   • ⏰ 시간 조건: {max_hold_hours}시간 초과 시 재평가")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST | v7.3.1 Pro Alert</i>")
        lines.append("<i>⚠️ Shadow Mode: 알림 전용 | 이후 알림은 시간이 아닌 '가격 도달' 기준으로 발송됩니다</i>")
        return "\n".join(lines)

    def _format_sl_trail(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        price = data.get("price", 0.0)
        _ = data.get("entry_price", price)
        old_stop = data.get("old_stop", 0.0)
        new_stop = data.get("new_stop", 0.0)
        _ = data.get("atr", 0.0)
        pnl = data.get("pnl", 0.0)
        advice = data.get("consensus") or data.get("advice")
        action = self._infer_advice_action(advice)
        if not advice:
            note = f"새 손절가({new_stop:,.0f}원) 밑으로 가격이 떨어지면 자동으로 매도 신호가 옵니다"
        else:
            note = advice.get("summary", "")
        lines = []
        lines.append(f"🔄 [손절가 상승] {ticker} - 트레일링 스탑 업데이트")
        lines += self._action_banner(action, price=price, note=note)
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>현재가</b>: <code>{price:,.0f}원</code>  |  <b>평가 손익</b>: <code>{pnl:+.1f}%</code>")
        lines.append("")
        lines.append("🛡️ <b>손절가 변경 내역 (가격 도달 시 자동 실행)</b>")
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
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST | 가격 기준 자동 트리거 (시간 무관)</i>")
        return "\n".join(lines)

    def _format_atr_spike(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        price = data.get("price", 0.0)
        _ = data.get("entry_price", 0.0)
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
        lines += self._action_banner(
            "WATCH",
            price=price,
            note="이 알림은 매도 신호가 아닙니다. 손절가/목표가만 자동 재조정되었습니다. 신규 진입은 변동성이 잦아들 때까지 보류를 권장합니다.",
        )
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>현재가</b>: <code>{price:,.0f}원</code>")
        lines.append("")
        lines.append("📊 <b>ATR 변동</b>")
        lines.append(f"   • 이전 ATR: <code>{old_atr:,.0f}원</code>")
        lines.append(
            f"   • 🔴 <b>현재 ATR</b>: <code>{new_atr:,.0f}원</code> (+{change_ratio:.0f}%, 변동성 수준: {level})"
        )
        lines.append("")
        lines.append("🔄 <b>자동 조정된 손절 (가격 도달 시 자동 실행)</b>")
        lines.append(f"   • 기존 손절: <code>{old_stop:,.0f}원</code>")
        lines.append(f"   • 🔴 <b>신규 손절</b>: <code>{new_stop:,.0f}원</code>")
        if tp_adjusted and tp2 > 0 and tp3 > 0:
            lines.append("")
            lines.append("🎯 <b>동적 익절가 조정</b>")
            lines.append(f"   • 🔹 TP2: <code>{tp2:,.0f}원</code> (ATR×5.0 적용)")
            lines.append(f"   • 🔹 TP3: <code>{tp3:,.0f}원</code> (ATR×7.0 적용)")
        lines.append("")
        lines.append("🧠 <b>액션 가이드</b>")
        lines.append("   • 기존 보유자: 포지션 유지, 위 신규 손절가만 참고")
        lines.append("   • 변동성이 부담되면 물량 20~30% 자율 축소도 가능(선택)")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST | v7.3.1</i>")
        return "\n".join(lines)

    def _format_tp_hit(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        tp_level = data.get("tp_level", 1)
        tp_price = data.get("tp_price", 0.0)
        price = data.get("price", 0.0)
        entry_price = data.get("entry_price", price)
        remaining_qty = data.get("remaining_qty", 1.0)
        rec_reason = data.get("recommendation_reason", "")
        tp_names = {1: "1차 (50%)", 2: "2차 (30%)", 3: "3차 (20%)"}
        tp_emojis = {1: "🎯", 2: "🎯", 3: "🏁"}
        try:
            tp_level_int = int(tp_level)
        except (TypeError, ValueError):
            tp_level_int = 1
        tp_name = tp_names.get(tp_level_int, f"{tp_level_int}차 (알 수 없음)")
        tp_emoji = tp_emojis.get(tp_level_int, "🏁")
        lines = []
        lines.append(f"{tp_emoji} [부분 익절 도달] {ticker} - {tp_name}")
        lines += self._action_banner(
            "EXECUTED", price=price, note=f"{tp_name} 목표가 도달 → 시스템이 자동으로 물량 일부를 이미 매도했습니다"
        )
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>현재가</b>: <code>{price:,.0f}원</code>  |  <b>목표가</b>: <code>{tp_price:,.0f}원</code>")
        lines.append(
            f"📊 <b>수익률</b>: <code>{((price - entry_price) / entry_price * 100) if entry_price else 0:+.2f}%</code>"
        )
        lines.append(f"📌 <b>남은 물량</b>: <code>{remaining_qty*100:.0f}%</code>")
        lines.append("")
        lines.append("🧠 <b>액션 가이드</b>")
        lines.append(f"   • {rec_reason}")
        if remaining_qty > 0:
            lines.append("   • 남은 물량은 트레일링 스탑으로 관리")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST</i>")
        return "\n".join(lines)

    def _format_exit(self, data: dict) -> str:
        ticker = html.escape(str(data.get("ticker", "N/A")))
        price = data.get("price", 0.0)
        entry_price = data.get("entry_price", price)
        pnl = data.get("pnl", 0.0)
        reason = data.get("reason", "알 수 없음")
        hold_time = self._calc_hold_time(data.get("entry_time"))
        lines = []
        lines.append(f"🔴 [포지션 청산 완료] {ticker}")
        lines += self._action_banner(
            "EXECUTED", price=price, note="포지션이 이미 자동 종료되었습니다. 추가로 하실 행동은 없습니다."
        )
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(
            f"💰 <b>청산가</b>: <code>{price:,.0f}원</code>  |  <b>진입가</b>: <code>{entry_price:,.0f}원</code>"
        )
        lines.append(f"📊 <b>최종 손익</b>: <code>{pnl:+.2f}%</code>  |  <b>보유 시간</b>: <code>{hold_time}</code>")
        lines.append("")
        lines.append("🛡️ <b>청산 사유</b>")
        lines.append(f"   • {reason}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST</i>")
        return "\n".join(lines)

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
        inferred_action = self._infer_advice_action(advice)
        vote_str = " | ".join(
            [
                f"기술:{votes.get('technical', 0):+.2f}",
                f"리스크:{votes.get('risk', 0):+.2f}",
                f"시간:{votes.get('time_value', 0):+.2f}",
                f"수급:{votes.get('micro', 0):+.2f}",
            ]
        )
        lines = []
        lines.append(f"{emoji} [🧠 합의 엔진] {ticker} - {action_label}")
        lines += self._action_banner(inferred_action, price=price, note=summary)
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"💰 <b>현재가</b>: <code>{price:,.0f}원</code>")
        lines.append(
            f"📊 <b>평가 손익</b>: <code>{pnl:+.1f}%</code>  |  <b>진입가</b>: <code>{entry_price:,.0f}원</code>"
        )
        lines.append("")
        lines.append("📋 <b>4대 개체 투표 결과</b> <i>(+는 매수/보유 우호, -는 매도 우호)</i>")
        lines.append(f"   {vote_str}")
        lines.append(
            f"   • 합의 점수: <code>{consensus_score:.2f}</code> "
            f"({'매도 우세' if consensus_score < -0.1 else '매수/보유 우세' if consensus_score > 0.1 else '팽팽함'})"
        )
        lines.append("")
        lines.append("🧠 <b>판단 근거</b>")
        for r in reasons[:4]:
            lines.append(f"   • {r}")
        if summary and not reasons:
            lines.append(f"   • {summary}")
        lines.append("")
        lines.append(f"🎯 <b>최종 권고</b>: {action_label}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>🕒 {datetime.now().strftime('%H:%M:%S')} KST | v7.3.1 Consensus (데이터 기반)</i>")
        return "\n".join(lines)

    def _calc_hold_time(self, entry_time_str: str) -> str:
        if not entry_time_str:
            return "N/A"
        try:
            entry_dt = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
            now = datetime.now()
            elapsed = now - entry_dt
            hours, remainder = divmod(elapsed.total_seconds(), 3600)
            minutes, _ = divmod(remainder, 60)
            return f"{int(hours)}h {int(minutes)}m" if hours > 0 else f"{int(minutes)}m"
        except:
            return "N/A"
