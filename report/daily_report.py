"""
report/daily_report.py - v6.1 FINAL (성능 로깅 + 분할 전송 위임)
- 리포트 생성 시간 측정 및 로깅 추가
- TelegramSender의 자동 분할 기능을 활용하도록 수동 자르기 제거
- ML/VaR 정보 포함 (v6.0 유지)
"""

import statistics
import time
import traceback
from datetime import datetime, timedelta

from core.logger import setup_logger
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender

logger = setup_logger("daily_report")


class DailyReportGenerator:
    def __init__(self, db_manager: DatabaseManager = None, telegram_sender: TelegramSender = None):
        self.db = db_manager or DatabaseManager()
        self.telegram = telegram_sender or TelegramSender()

    async def generate_and_send(self):
        """전략 데일리 브리프 생성 및 발송 (v6.1 - 성능 로깅)"""
        start_time = time.time()
        logger.info("📊 [v6.1] 데일리 브리프 생성 시작...")

        try:
            today = datetime.now().strftime("%Y-%m-%d")
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

            decisions = await self.db.get_decisions_by_date(today)
            yesterday_decisions = await self.db.get_decisions_by_date(yesterday)
            weights = await self.db.get_weights()

            exits = [d for d in decisions if d.get("action") == "EXIT"]
            updates = [d for d in decisions if d.get("action") == "TRAILING_STOP_UPDATE"]

            regime, regime_desc, confidence = self._diagnose_regime(decisions)

            lines = []
            lines.append("<b>🏛️ [QUANT DESK] 데일리 전략 브리프 v6.1</b>")
            lines.append(f"<i>{today} (KST) | Market Regime: {regime}</i>")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")

            # --- SECTION 1: EXECUTIVE SUMMARY ---
            lines.append("<b>📌 1. 오늘의 요약 & 전일 성과</b>")
            if decisions:
                total = len(decisions)
                buy_cnt = sum(1 for d in decisions if d["action"] == "BUY")
                sell_cnt = sum(1 for d in decisions if d["action"] == "SELL")
                hold_cnt = sum(1 for d in decisions if d["action"] == "HOLD")
                avg_score = statistics.mean([d["score"] for d in decisions]) if decisions else 0.0
                avg_conf = statistics.mean([d["confidence"] for d in decisions]) if decisions else 0.0

                ml_scores = [d.get("ml_score", 0.5) for d in decisions if d.get("ml_score") is not None]
                risk_adjs = [
                    d.get("risk_adjustment_factor", 1.0)
                    for d in decisions
                    if d.get("risk_adjustment_factor") is not None
                ]
                avg_ml = statistics.mean(ml_scores) if ml_scores else 0.5
                avg_risk_adj = statistics.mean(risk_adjs) if risk_adjs else 1.0

                if buy_cnt > sell_cnt * 1.8:
                    bias = "🟢 강한 매수 (과열 주의)"
                elif buy_cnt > sell_cnt * 1.2:
                    bias = "🟡 매수 우위 (추세)"
                elif sell_cnt > buy_cnt * 1.8:
                    bias = "🔴 강한 매도 (하방 위험)"
                elif sell_cnt > buy_cnt * 1.2:
                    bias = "🟠 매도 우위 (조정)"
                else:
                    bias = "⚪ 중립 (관망)"

                lines.append(f"• <b>{total}개 신호</b> | 매수 {buy_cnt} / 매도 {sell_cnt} / 관망 {hold_cnt}")
                lines.append(f"• 평균 확신도: <b>{avg_conf:.1%}</b> | 평균 점수: <code>{avg_score:.3f}</code>")
                lines.append(f"• 🧠 ML 평균 예측: <b>{avg_ml:.1%}</b> | VaR 조정 계수: <b>{avg_risk_adj:.2f}</b>")
                lines.append(f"• 시장 심리: <b>{bias}</b> | 국면 신뢰도: {confidence:.0%}")

                if yesterday_decisions:
                    y_buy = sum(1 for d in yesterday_decisions if d["action"] == "BUY")
                    y_sell = sum(1 for d in yesterday_decisions if d["action"] == "SELL")
                    lines.append(f"• 📊 전일 신호: 매수 {y_buy}건 / 매도 {y_sell}건 (추적 중)")
                else:
                    lines.append("• 📊 전일 신호: 데이터 없음 (초기 실행)")
            else:
                lines.append("• <i>금일 신호 없음 (장 마감 또는 횡보)</i>")
                if yesterday_decisions:
                    lines.append(f"• 📊 전일 신호: {len(yesterday_decisions)}건 발생")
            lines.append("")

            # --- SECTION 2: TRAILING STOP ---
            if exits or updates:
                lines.append("<b>🔄 2. 트레일링 스탑 이벤트</b>")
                if exits:
                    lines.append("   <b>[청산 발생]</b>")
                    for e in exits[:3]:
                        ticker = e.get("ticker", "")
                        pnl = e.get("pnl", 0.0)
                        lines.append(f"   • 🔴 <b>{ticker}</b> 청산 (손익: {pnl:+.1f}%)")
                if updates:
                    lines.append("   <b>[손절 상승]</b>")
                    for u in updates[:2]:
                        ticker = u.get("ticker", "")
                        old_s = u.get("old_stop", 0)
                        new_s = u.get("new_stop", 0)
                        lines.append(f"   • 📈 {ticker} 손절 상승: {old_s:,.0f} → {new_s:,.0f}원")
                lines.append("")

            # --- SECTION 3: DYNAMIC POSITIONING ---
            lines.append("<b>🎯 3. 동적 3-Tier 포지셔닝 (ML 반영)</b>")
            if decisions:
                avg_score = statistics.mean([d["score"] for d in decisions]) if decisions else 0.5
                avg_ml = (
                    statistics.mean([d.get("ml_score", 0.5) for d in decisions if d.get("ml_score") is not None])
                    if ml_scores
                    else 0.5
                )
                core = min(80, int(45 + avg_score * 30 + avg_ml * 20))
                tactical = max(5, int(30 - avg_score * 15 - avg_ml * 10))
                optionality = 5
                cash = 100 - core - tactical - optionality
                lines.append(f"   • [Core] 반도체·인프라: <b>{core}%</b> (점수 {avg_score:.0%} + ML {avg_ml:.0%})")
                lines.append(f"   • [Tactical] 피지컬 AI·로봇: <b>{tactical}%</b>")
                lines.append(f"   • [Optionality] 양자·보안: <b>{optionality}%</b>")
                lines.append(f"   • 현금: <b>{cash}%</b>")
            else:
                lines.append("   • Core: 50% | Tactical: 20% | 현금: 30% (신호 부재)")
            lines.append("")

            # --- SECTION 4: RISK ---
            lines.append("<b>⚠️ 4. 오늘의 3대 리스크</b>")
            risks = self._get_daily_risks(regime, decisions)
            for r in risks:
                lines.append(f"• {r}")
            lines.append("")

            # --- SECTION 5: FACTOR DRIFT ---
            lines.append("<b>⚙️ 5. 팩터 드리프트 (7일 EMA)</b>")
            if weights:
                drift_str = ", ".join([f"{k}:{v:.2f}" for k, v in weights.items()])
                lines.append(f"• <code>{drift_str}</code>")
                top_factor = max(weights, key=weights.get)
                lines.append(f"• <b>↑ 우세 팩터:</b> {top_factor} (가중치 {weights[top_factor]:.2f})")
            else:
                lines.append("• <i>초기 가중치 (학습 전)</i>")
            lines.append("")

            # --- SECTION 6: ACTION ITEMS ---
            lines.append("<b>📋 6. 오늘의 액션 아이템</b>")
            actions = self._get_action_items(regime, decisions)
            for a in actions:
                lines.append(f"• {a}")
            lines.append("")

            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("<i>📌 풀버전(14p)은 매주 월요일 06:00 PDF 발송</i>")
            lines.append("<i>⚠️ 투자자문 아님 | 책임은 투자자 본인</i>")
            lines.append(f"<i>📊 브리프 ID: {datetime.now().strftime('%Y%m%d')}-{len(decisions)}</i>")

            full_msg = "\n".join(lines)

            # 🔥 v6.1: TelegramSender가 자동 분할 처리하므로 그냥 전송
            await self.telegram.send_raw(full_msg)

            elapsed = time.time() - start_time
            logger.info(f"📊 데일리 브리프 전송 완료 (신호 {len(decisions)}건, 소요 {elapsed:.2f}초)")

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ 데일리 리포트 생성 실패 (소요 {elapsed:.2f}초): {e}")
            logger.error(traceback.format_exc())
            raise

    # ============================================================
    # 내부 헬퍼 함수 (기존 유지)
    # ============================================================
    def _diagnose_regime(self, decisions: list[dict]) -> tuple[str, str, float]:
        if not decisions:
            return "🔄 횡보", "신호 부재로 관망 유지", 0.3
        buy_ratio = sum(1 for d in decisions if d["action"] == "BUY") / len(decisions)
        avg_score = statistics.mean([d["score"] for d in decisions]) if decisions else 0.0
        avg_conf = statistics.mean([d["confidence"] for d in decisions]) if decisions else 0.0
        composite = (buy_ratio * 0.6 + avg_score * 0.4) * avg_conf
        if composite > 0.6 and buy_ratio > 0.55:
            return "🚀 강한 상승 (Risk-On)", "AI·로봇 축 자금 유입", min(0.95, composite)
        elif composite > 0.4 and buy_ratio > 0.4:
            return "📈 우상향 추세", "Core 중심 리스크 온", min(0.85, composite + 0.1)
        elif composite < 0.2 or buy_ratio < 0.25:
            return "📉 약세 (Risk-Off)", "차익실현·방어 심리", min(0.85, 1.0 - composite)
        else:
            return "⚖️ 중립·전환", "멀티사이클 중첩 구간", 0.6

    def _get_daily_risks(self, regime: str, decisions: list[dict]) -> list[str]:
        base = [
            "① 전력 인허가 지연 → AI 인프라 상단 제약",
            "② HBM 가격 피크아웃 조기화 가능성",
            "③ 로봇 파일럿→반복 수주 전환 증거 부재",
        ]
        if "상승" in regime:
            return [
                "① 과열된 로봇 섹터 밸류에이션 조정 리스크",
                "② NVIDIA 가이던스 하향 시 반도체 전체 멀티플 압축",
                "③ 외국인 수급 급변 시 변동성 확대",
            ]
        elif "약세" in regime:
            return [
                "① 추가 하방 시 숏스퀴즈 가능성 (반등)",
                "② 환율 급등 시 외국인 자금 이탈 가속화",
                "③ 지정학적 리스크 재부각 (대만·반도체)",
            ]
        return base

    def _get_action_items(self, regime: str, decisions: list[dict]) -> list[str]:
        if "상승" in regime:
            return [
                "✅ Core(반도체) 비중 유지, 추세 추종",
                "✅ Tactical(로봇)은 조정 시 분할 매수",
                "✅ Optionality(양자)는 5% 이하로 유지",
            ]
        elif "약세" in regime:
            return ["⚠️ Core 비중을 50%로 축소 (방어)", "⚠️ 현금 비중 20% 이상 확보", "⚠️ 로봇·양자 비중 긴급 점검"]
        else:
            return [
                "🔍 Core 유지 (60%), Tactical 20%, 현금 15%",
                "🔍 외국인 수급·TSMC 발언 모니터링 강화",
                "🔍 로봇 이벤트(실적발표) 전까지 대기",
            ]
