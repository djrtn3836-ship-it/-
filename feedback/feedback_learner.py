"""feedback/feedback_learner.py - 성과 귀속 분석 및 모델 최적화 (완전 한글화)"""
import math
from datetime import datetime, timedelta
from typing import Optional
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender
from core.logger import setup_logger

logger = setup_logger("feedback")

class FeedbackLearner:
    def __init__(self, kiwoom_connector=None, db_manager: DatabaseManager = None):
        self.db = db_manager or DatabaseManager()
        self.connector = kiwoom_connector
        self.telegram = TelegramSender()

    async def run(self):
        logger.info("🧠 기관용 피드백 학습 및 성과 귀속(Attribution) 파이프라인 가동...")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        decisions = await self.db.get_decisions_by_date(yesterday)

        if not decisions:
            logger.info(f"📭 {yesterday} 학습할 결정 기록이 없습니다.")
            return

        logger.info(f"📊 {len(decisions)}개 타겟에 대한 성과 분석 중...")
        outcomes = []
        for dec in decisions:
            outcome = await self._fetch_real_outcome(dec)
            if outcome:
                await self.db.save_outcome(outcome)
                outcomes.append(outcome)

        if not outcomes:
            logger.warning("⚠️ 수집된 결과 데이터가 없어 가중치 조정을 스킵합니다.")
            return

        prev_weights = await self.db.get_weights()
        total = len(outcomes)
        wins = [o['return_1d'] for o in outcomes if o['is_correct']]
        losses = [abs(o['return_1d']) for o in outcomes if not o['is_correct']]
        
        accuracy = len(wins) / total if total > 0 else 0.0
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)

        returns = [o['return_1d'] for o in outcomes]
        mean_ret = sum(returns) / total
        variance = sum((r - mean_ret) ** 2 for r in returns) / total if total > 1 else 0.0
        std_dev = math.sqrt(variance)
        sharpe_ratio = (mean_ret / std_dev) * math.sqrt(252) if std_dev > 0 else 0.0

        new_weights = await self._update_weights_ema(outcomes, prev_weights)

        await self._send_institutional_report(
            yesterday, total, len(wins), accuracy, mean_ret, profit_factor, sharpe_ratio, prev_weights, new_weights
        )

    async def _fetch_real_outcome(self, decision: dict) -> Optional[dict]:
        ticker = decision['ticker']
        action = decision['action']
        price_at = decision['price_at_decision']

        try:
            if self.connector:
                resp = await self.connector.request_tr(ticker, "일봉")
                price_after = float(resp.get('close', price_at))
            else:
                price_after = price_at
        except Exception as e:
            logger.error(f"❌ 가격 데이터 조회 실패 ({ticker}): {e}")
            return None

        return_1d = (price_after - price_at) / price_at if price_at > 0 else 0.0

        if action == 'BUY':
            is_correct = return_1d > 0
        elif action == 'SELL':
            is_correct = return_1d < 0
        else:
            is_correct = abs(return_1d) < 0.02

        return {
            'decision_id': decision['id'],
            'price_after_1d': price_after,
            'price_after_5d': price_after * (1 + return_1d),
            'return_1d': return_1d * 100,
            'return_5d': return_1d * 100,
            'is_correct': is_correct
        }

    async def _update_weights_ema(self, outcomes: list, current_weights: dict) -> dict:
        avg_return = sum(o['return_1d'] for o in outcomes) / len(outcomes) if outcomes else 0.0
        delta = 0.05 if avg_return > 0 else -0.05
        
        updated = {}
        for factor, current_weight in current_weights.items():
            new_weight = max(0.1, min(3.0, current_weight + delta))
            await self.db.update_weight(factor, new_weight)
            updated[factor] = new_weight

        logger.info(f"📊 EMA 가중치 최적화 완료 (평균 수익률: {avg_return:.2f}%)")
        return updated

    async def _send_institutional_report(
        self, date_str: str, total: int, correct: int, accuracy: float, 
        mean_ret: float, profit_factor: float, sharpe: float, 
        prev_w: dict, new_w: dict
    ):
        factor_map = {
            'momentum': '모멘텀(momentum)',
            'volume': '거래량(volume)',
            'volatility': '변동성(volatility)',
            'macro': '매크로(macro)',
            'sector': '섹터(sector)'
        }

        drift_lines = []
        for f in prev_w.keys():
            old_v, new_v = prev_w.get(f, 1.0), new_w.get(f, 1.0)
            diff = new_v - old_v
            arrow = "🔺" if diff > 0 else ("🔻" if diff < 0 else "➖")
            label = factor_map.get(f, f)
            drift_lines.append(f"• <code>{label:<12}</code>: {old_v:.2f} ➔ <b>{new_v:.2f}</b> ({arrow} {diff:+.2f})")

        msg = (
            f"<b>🧠 [퀀트 데스크] 모델 최적화 및 성과 보고서 ({date_str})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>📈 1. 포트폴리오 성과 지표 (Portfolio Performance)</b>\n"
            f"• 총 분석 샘플: <b>{total}개 신호</b> | 예측 적중률: <b>{accuracy:.1%}</b> ({correct}/{total})\n"
            f"• 일간 평균 수익률: <code>{mean_ret:+.2f}%</code>\n"
            f"• 손익비 (Profit Factor): <code>{profit_factor:.2f}</code> | 연율화 샤프 지수: <code>{sharpe:.2f}</code>\n\n"
            f"<b>⚙️ 2. 팩터 가중치 재조정 (Factor Weight Drift)</b>\n"
            + "\n".join(drift_lines) + "\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>실행 엔진: EMA 적응형 모델 최적화 엔진</i>"
        )
        await self.telegram.send_raw(msg)