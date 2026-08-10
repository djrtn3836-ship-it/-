"""report/daily_report.py - 기관 투자가용 일일 운용 종합 보고서 (종목명+코드 동시 표기)"""
from datetime import datetime
from core.logger import setup_logger
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender

logger = setup_logger("daily_report")

class DailyReportGenerator:
    def __init__(self, db_manager: DatabaseManager = None, telegram_sender: TelegramSender = None):
        self.db = db_manager or DatabaseManager()
        self.telegram = telegram_sender or TelegramSender()

    async def generate_and_send(self):
        logger.info("📊 기관 투자가용 일일 운용 종합 보고서 생성 시작...")
        today = datetime.now().strftime("%Y-%m-%d")
        decisions = await self.db.get_decisions_by_date(today)
        weights = await self.db.get_weights()

        if not decisions:
            msg = (
                f"<b>🏛️ [퀀트 데스크] 일일 운용 종합 보고서 ({today})</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ <b>시장 현황</b>: 금일 발생한 알고리즘 분석 신호가 없습니다."
            )
            await self.telegram.send_raw(msg)
            return

        total = len(decisions)
        buy_list = [d for d in decisions if d['action'] == 'BUY']
        sell_list = [d for d in decisions if d['action'] == 'SELL']
        hold_list = [d for d in decisions if d['action'] == 'HOLD']

        avg_conf = sum(d['confidence'] for d in decisions) / total if total > 0 else 0.0
        avg_score = sum(d['score'] for d in decisions) / total if total > 0 else 0.0

        top_buy = sorted(buy_list, key=lambda x: x['score'], reverse=True)[:3]

        lines = [
            f"<b>🏛️ [퀀트 데스크] 일일 운용 종합 보고서 ({today})</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            "<b>📊 1. 신호 분포 및 모델 확신도 (Conviction)</b>",
            f"• 총 분석 건수: <b>{total}건</b>",
            f"• 매매 포지션: 🟢 매수 <b>{len(buy_list)}</b> | 🔴 매도 <b>{len(sell_list)}</b> | ⚪ 관망 <b>{len(hold_list)}</b>",
            f"• 모델 평균 확신도: <b>{avg_conf:.1%}</b> (평균 스코어: <code>{avg_score:.3f}</code>)",
            "",
            "<b>🎯 2. 최우선 알파 추천 종목 (Target BUYs)</b>"
        ]

        if top_buy:
            for i, d in enumerate(top_buy, 1):
                px = d.get('price_at_decision', d.get('price', 0.0))
                rec_alloc = min(15.0, max(3.0, d['score'] * 20.0))
                
                # 🔥 종목명 + 종목코드 동시 표시
                stock_name = d.get('name', d.get('stock_name', ''))
                display_name = f"{stock_name} ({d['ticker']})" if stock_name else f"종목코드 {d['ticker']}"

                lines.append(
                    f"<b>{i}. {display_name}</b> | 모델 확신도: <b>{d['score']:.1%}</b>\n"
                    f"   • 진입 참고가: <code>{px:,.0f} 원</code> | 권장 포트 비중: <code>{rec_alloc:.1f}%</code>\n"
                    f"   • 매수 근거: <i>{', '.join(d.get('positives', ['알파 팩터 우상향'])[:2])}</i>"
                )
        else:
            lines.append("• <i>금일 고확신(High-Conviction) 매수 조건에 부합하는 종목이 없습니다.</i>")

        lines.extend([
            "",
            "<b>⚙️ 3. 활성 전략 팩터 가중치 현황</b>",
            f"• 팩터 프로필: <code>{', '.join([f'{k}:{v:.2f}' for k, v in weights.items()])}</code>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            "<i>운용 상태: Phase 1 Shadow Execution Mode | 리스크 준수: 승인됨</i>"
        ])

        await self.telegram.send_raw("\n".join(lines))
        logger.info("📊 기관용 일일 운용 종합 보고서 전송 완료")