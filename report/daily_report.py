"""
report/daily_report.py - v5.2.0 ULTIMATE
Telegram 전략 데일리 브리프 (4096자 한계 내 최대 압축)
- Executive Summary, 3-Tier, Risk, Factor Drift, Today's Action Items
"""
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
import statistics

from core.logger import setup_logger
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender

logger = setup_logger("daily_report")


class DailyReportGenerator:
    def __init__(self, db_manager: DatabaseManager = None, telegram_sender: TelegramSender = None):
        self.db = db_manager or DatabaseManager()
        self.telegram = telegram_sender or TelegramSender()

    async def generate_and_send(self):
        """전략 데일리 브리프 생성 및 발송 (PDF 1~6장 압축본)"""
        logger.info("📊 [ULTIMATE] 데일리 브리프 생성 시작...")
        today = datetime.now().strftime("%Y-%m-%d")
        
        # --- 1. 데이터 로드 ---
        decisions = await self.db.get_decisions_by_date(today)
        weights = await self.db.get_weights()
        
        # --- 2. 시장 국면 진단 ---
        regime, regime_desc, confidence = self._diagnose_regime(decisions)
        
        # --- 3. 본문 구성 (4096자 이내 최적화) ---
        lines = []
        
        # 헤더
        lines.append(f"<b>🏛️ [QUANT DESK] 데일리 전략 브리프</b>")
        lines.append(f"<i>{today} (KST) | Market Regime: {regime}</i>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        # --- SECTION 1: EXECUTIVE SUMMARY ---
        lines.append("<b>📌 1. 오늘의 요약 (Today's Takeaway)</b>")
        if decisions:
            total = len(decisions)
            buy_cnt = sum(1 for d in decisions if d['action'] == 'BUY')
            sell_cnt = sum(1 for d in decisions if d['action'] == 'SELL')
            hold_cnt = sum(1 for d in decisions if d['action'] == 'HOLD')
            avg_score = statistics.mean([d['score'] for d in decisions]) if decisions else 0.0
            avg_conf = statistics.mean([d['confidence'] for d in decisions]) if decisions else 0.0
            
            # 신호 강도 평가
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
            lines.append(f"• 시장 심리: <b>{bias}</b> | 국면 신뢰도: {confidence:.0%}")
        else:
            lines.append("• <i>금일 신호 없음 (장 마감 또는 횡보)</i>")
        
        lines.append("")

        # --- SECTION 2: 3-TIER POSITIONING ---
        lines.append("<b>🎯 2. 3-Tier 포지셔닝</b>")
        top_buy = sorted([d for d in decisions if d['action'] == 'BUY'], key=lambda x: x['score'], reverse=True)[:3]
        top_sell = sorted([d for d in decisions if d['action'] == 'SELL'], key=lambda x: x['score'], reverse=True)[:2]
        
        # Core
        lines.append("   <b>[Core] 반도체·인프라 (60~70%)</b>")
        if top_buy:
            for d in top_buy[:2]:
                name = self._safe_name(d)
                lines.append(f"   • {name} — 확신도 <b>{d['score']:.1%}</b> (병목·수급 우위)")
        else:
            lines.append("   • <i>현재 Core 추천 없음</i>")
        
        # Tactical
        lines.append("   <b>[Tactical] 피지컬 AI·로봇 (20~25%)</b>")
        tactical_candidates = [d for d in decisions if d['action'] == 'BUY' and d['score'] > 0.55][:2]
        if tactical_candidates:
            for d in tactical_candidates:
                name = self._safe_name(d)
                lines.append(f"   • {name} — 서사 모멘텀 <b>주의</b> (추격 자제)")
        else:
            lines.append("   • <i>현재 Tactical 신호 없음</i>")
        
        # Optionality
        lines.append("   <b>[Optionality] 양자·보안 (5~10%)</b>")
        lines.append("   • <i>옵션성 유지 (PQC 예산화 모니터링)</i>")
        
        # Sell 리스트
        if top_sell:
            lines.append("   <b>⚠️ 관심 종목</b>")
            for d in top_sell:
                name = self._safe_name(d)
                lines.append(f"   • {name} — 매도 신호 (점수: {d['score']:.1%})")
        lines.append("")

        # --- SECTION 3: RISK MATRIX ---
        lines.append("<b>⚠️ 3. 오늘의 3대 리스크</b>")
        risks = self._get_daily_risks(regime, decisions)
        for r in risks:
            lines.append(f"• {r}")
        lines.append("")

        # --- SECTION 4: FACTOR DRIFT ---
        lines.append("<b>⚙️ 4. 팩터 드리프트 (7일 EMA)</b>")
        if weights:
            drift_str = ", ".join([f"{k}:{v:.2f}" for k, v in weights.items()])
            lines.append(f"• <code>{drift_str}</code>")
            # 추가: 가장 많이 상승한 팩터 표시
            top_factor = max(weights, key=weights.get)
            lines.append(f"• <b>↑ 우세 팩터:</b> {top_factor} (가중치 {weights[top_factor]:.2f})")
        else:
            lines.append("• <i>초기 가중치 (학습 전)</i>")
        lines.append("")

        # --- SECTION 5: TODAY'S ACTION ITEMS ---
        lines.append("<b>📋 5. 오늘의 액션 아이템</b>")
        actions = self._get_action_items(regime, decisions)
        for a in actions:
            lines.append(f"• {a}")
        lines.append("")

        # --- FOOTER ---
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("<i>📌 풀버전(14p)은 매주 월요일 06:00 PDF 발송</i>")
        lines.append("<i>⚠️ 투자자문 아님 | 책임은 투자자 본인</i>")
        lines.append(f"<i>📊 브리프 ID: {datetime.now().strftime('%Y%m%d')}-{len(decisions)}</i>")

        # 전송
        full_msg = "\n".join(lines)
        if len(full_msg) > 4000:
            full_msg = full_msg[:3950] + "\n... (메시지 길이 초과, 일부 생략)"
        
        await self.telegram.send_raw(full_msg)
        logger.info(f"📊 데일리 브리프 전송 완료 (신호 {len(decisions)}건)")

    # ============================================================
    # 내부 헬퍼 함수
    # ============================================================
    
    def _safe_name(self, decision: Dict) -> str:
        name = decision.get('name', decision.get('stock_name', ''))
        ticker = decision.get('ticker', '')
        if name:
            return f"{name} ({ticker})"
        return ticker

    def _diagnose_regime(self, decisions: List[Dict]) -> Tuple[str, str, float]:
        """시장 국면 진단"""
        if not decisions:
            return "🔄 횡보", "신호 부재로 관망 유지", 0.3
        
        buy_ratio = sum(1 for d in decisions if d['action'] == 'BUY') / len(decisions)
        avg_score = statistics.mean([d['score'] for d in decisions]) if decisions else 0.0
        avg_conf = statistics.mean([d['confidence'] for d in decisions]) if decisions else 0.0
        
        # 복합 점수
        composite = (buy_ratio * 0.6 + avg_score * 0.4) * avg_conf
        
        if composite > 0.6 and buy_ratio > 0.55:
            return "🚀 강한 상승 (Risk-On)", "AI·로봇 축 자금 유입", min(0.95, composite)
        elif composite > 0.4 and buy_ratio > 0.4:
            return "📈 우상향 추세", "Core 중심 리스크 온", min(0.85, composite + 0.1)
        elif composite < 0.2 or buy_ratio < 0.25:
            return "📉 약세 (Risk-Off)", "차익실현·방어 심리", min(0.85, 1.0 - composite)
        else:
            return "⚖️ 중립·전환", "멀티사이클 중첩 구간", 0.6

    def _get_daily_risks(self, regime: str, decisions: List[Dict]) -> List[str]:
        """리스트 3개 반환"""
        base = [
            "① 전력 인허가 지연 → AI 인프라 상단 제약",
            "② HBM 가격 피크아웃 조기화 가능성",
            "③ 로봇 파일럿→반복 수주 전환 증거 부재"
        ]
        
        if "상승" in regime:
            return [
                "① 과열된 로봇 섹터 밸류에이션 조정 리스크",
                "② NVIDIA 가이던스 하향 시 반도체 전체 멀티플 압축",
                "③ 외국인 수급 급변 시 변동성 확대"
            ]
        elif "약세" in regime:
            return [
                "① 추가 하방 시 숏스퀴즈 가능성 (반등)",
                "② 환율 급등 시 외국인 자금 이탈 가속화",
                "③ 지정학적 리스크 재부각 (대만·반도체)"
            ]
        return base

    def _get_action_items(self, regime: str, decisions: List[Dict]) -> List[str]:
        """액션 아이템 3개"""
        if "상승" in regime:
            return [
                "✅ Core(반도체) 비중 유지, 추세 추종",
                "✅ Tactical(로봇)은 조정 시 분할 매수 (추격 자제)",
                "✅ Optionality(양자)는 5% 이하로 유지"
            ]
        elif "약세" in regime:
            return [
                "⚠️ Core 비중을 50%로 축소 (방어)",
                "⚠️ 현금 비중 20% 이상 확보, 헤지 고려",
                "⚠️ 로봇·양자 비중 긴급 점검 (과도 노출 자제)"
            ]
        else:
            return [
                "🔍 Core 유지 (60%), Tactical 20%, 현금 15%",
                "🔍 외국인 수급·TSMC 발언 모니터링 강화",
                "🔍 로봇 이벤트(실적발표) 전까지 대기"
            ]