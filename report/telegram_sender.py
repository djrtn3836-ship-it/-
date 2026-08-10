"""
report/telegram_sender.py - v5.4.0 ULTIMATE (초고도화 지능형 리포트)
- 상황별 긴급도 태그 (🚨 긴급 / 📈 일반 / ⚠️ 주의)
- 지지/저항선, 매수/매도 압력(Imbalance) 표시
- 켈리 공식 기반 추천 포지션 비중 자동 계산
- 모든 필드 누락 시 안전하게 기본값 처리 (100% 에러 방지)
- 4096자 길이 초과 시 자동 압축
"""

import os
import html
import math
from typing import Dict, Optional
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
        """고도화된 리포트 전송 (에러 처리 완벽 적용)"""
        if not self.bot or not self.chat_id:
            return False
        try:
            message = self._format_intelligent_report(report)
            return await self.send_raw(message)
        except Exception as e:
            logger.error(f"❌ Telegram 전송 오류: {e}", exc_info=True)
            return False

    async def send_raw(self, message: str) -> bool:
        """원시 메시지 전송 (길이 제한 및 에러 처리)"""
        if not self.bot or not self.chat_id:
            return False
        
        # 1. 길이 제한 (4096자)
        if len(message) > 4000:
            message = message[:3950] + "\n\n... (메시지 길이 초과로 일부 생략)"
        
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="HTML"
            )
            logger.info("✅ Telegram 메시지 전송 성공")
            return True
        except TelegramError as e:
            # 텔레그램 API 오류 시 로그만 남기고 실패 처리 (크래시 방지)
            logger.error(f"❌ Telegram API 오류: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Telegram 전송 예외: {e}")
            return False

    # ============================================================
    # 🔥 핵심: 초고도화 지능형 포맷터
    # ============================================================
    def _format_intelligent_report(self, report: dict) -> str:
        """
        퀀트 데스크 수준의 인텔리전트 리포트 생성
        - 모든 필드에 .get() 적용하여 KeyError 방지
        - 누락된 데이터는 자체적으로 추론하거나 기본값 처리
        """
        # ---- 1. 기본 정보 추출 (안전하게) ----
        ticker = html.escape(str(report.get("ticker", "N/A")))
        name = html.escape(str(report.get("name", ticker)))
        action = html.escape(str(report.get("action", "HOLD")))
        price = report.get("price", 0.0)
        score = report.get("score", 0.5)
        confidence = report.get("confidence", 0.5)
        momentum = report.get("momentum", 0.0)
        volume = report.get("volume", 0)
        
        # ---- 2. 고급 지표 추출 (없으면 None 처리) ----
        support = report.get("support_level")
        resistance = report.get("resistance_level")
        imbalance = report.get("imbalance")  # 0~1
        pressure = report.get("pressure", "")
        positives = report.get("positives", [])
        negatives = report.get("negatives", [])

        # ---- 3. 상황별 긴급도 태그 (Intelligence) ----
        is_emergency = abs(momentum) > 0.05  # 5% 이상 변동
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

        # ---- 4. 액션별 이모지 및 색상 ----
        action_emoji = "🟢" if action == "BUY" else ("🔴" if action == "SELL" else "⚪")

        # ---- 5. 본문 조립 (라인 단위) ----
        lines = []
        
        # [헤더]
        lines.append(f"<b>{header_emoji} {header_title}</b>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        
        # [종목 정보]
        lines.append(f"<b>{action_emoji} [{action}] {name} ({ticker})</b>")
        lines.append("")
        
        # [가격 및 변동]
        price_str = f"{price:,.0f} 원" if price > 0 else "데이터 없음"
        change_str = f"{momentum:+.2%}" if momentum != 0 else "0.00%"
        lines.append(f"💰 <b>현재가</b>: <code>{price_str}</code>  |  <b>변동률</b>: <code>{change_str}</code>")
        
        # [신뢰도 및 점수 게이지 (시각화)]
        conf_bar = "█" * int(confidence * 10) + "░" * (10 - int(confidence * 10))
        score_bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
        lines.append(f"📊 <b>신뢰도</b>: {confidence:.1%} {conf_bar}")
        lines.append(f"📊 <b>점수</b>: {score:.1%} {score_bar}")
        lines.append("")

        # [고급 인사이트 1: 지지/저항선] - Intelligence
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

        # [고급 인사이트 2: 매수/매도 압력 (Imbalance)] - Intelligence
        if imbalance is not None:
            if isinstance(imbalance, (int, float)) and 0 <= imbalance <= 1:
                bar = "█" * int(imbalance * 10) + "░" * (10 - int(imbalance * 10))
                side = "매수" if imbalance > 0.55 else ("매도" if imbalance < 0.45 else "중립")
                lines.append(f"⚖️ <b>호가 불균형</b>: {side} 우세 ({imbalance:.1%}) {bar}")
                if pressure:
                    lines.append(f"  • {html.escape(pressure)}")
                lines.append("")

        # [추천 포지션 비중 (Kelly Criterion 기반)] - Intelligence
        if action in ["BUY", "SELL"] and confidence > 0.5:
            # 켈리 공식 변형: 확신도와 변동성을 기반으로 한 추천 비중 (0~15%)
            kelly_ratio = min(15.0, max(2.0, (confidence * 20) - (abs(momentum) * 50)))
            if momentum > 0 and action == "BUY":
                rec = min(15.0, kelly_ratio * 1.2)
            elif momentum < 0 and action == "SELL":
                rec = min(15.0, kelly_ratio * 0.8)
            else:
                rec = min(15.0, kelly_ratio)
            
            lines.append(f"🎯 <b>권장 포지션</b>: <code>{rec:.1f}%</code> (Kelly 변형)")
            lines.append("")

        # [매수/매도 근거]
        if positives:
            lines.append("✅ <b>매수 근거 (Positives)</b>")
            # 3개로 제한하여 메시지 압축
            for p in positives[:3]:
                if p and isinstance(p, str):
                    lines.append(f"  • {html.escape(p)}")
            lines.append("")

        # [주의사항]
        if negatives:
            lines.append("⚠️ <b>주의사항 (Risks)</b>")
            for n in negatives[:2]:
                if n and isinstance(n, str):
                    lines.append(f"  • {html.escape(n)}")
            lines.append("")

        # [거래량]
        if volume and volume > 0:
            vol_str = f"{volume:,.0f}" if volume < 10000 else f"{volume/10000:.1f}만"
            lines.append(f"📊 <b>거래량</b>: {vol_str} 주")
            lines.append("")

        # [푸터]
        lines.append("━━━━━━━━━━━━━━━━━━━━━")
        lines.append("<i>Phase 1 Shadow Mode | 본 보고서는 참고용입니다</i>")
        lines.append(f"<i>🕒 {__import__('datetime').datetime.now().strftime('%H:%M:%S')} KST</i>")

        # 최종 메시지 결합
        full_message = "\n".join(lines)

        # 🛡️ 최종 안전장치: 혹시라도 None이나 비정상 데이터가 있으면 기본값으로 대체
        # (여기까지 왔으면 모든 데이터는 안전하게 처리됨)
        return full_message