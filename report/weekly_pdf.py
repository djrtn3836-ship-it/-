"""
report/weekly_pdf.py - v6.1 FINAL (성능 로깅 + 폰트 폴백 강화)
- PDF 생성 시간 측정 및 로깅 추가
- 폰트 로드 실패 시 Helvetica로 안전하게 폴백 (font_utils 연동)
- ML/VaR 정보 포함 (v6.0 유지)
"""

import os
import statistics
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.font_utils import FONT_BOLD, FONT_NAME, register_korean_fonts
from core.logger import setup_logger
from data.dart_connector import DartConnector
from data.db_manager import DatabaseManager
from data.news_crawler import NewsCrawler

logger = setup_logger("weekly_pdf")

PDF_DIR = Path(__file__).parent.parent / "reports"
PDF_DIR.mkdir(parents=True, exist_ok=True)


class WeeklyPDFGenerator:
    def __init__(self, db_manager: DatabaseManager = None, kiwoom_connector=None):
        self.db = db_manager or DatabaseManager()
        self.kiwoom = kiwoom_connector

        load_dotenv()
        self.dart_api_key = os.getenv("DART_API_KEY")
        self.dart = DartConnector(self.dart_api_key) if self.dart_api_key else None
        if not self.dart:
            logger.warning("⚠️ DART API 키 없음 → 재무 데이터 제외")

        self.news = NewsCrawler()

        self.styles = None
        self.story = []

        # 🔥 v6.1: 폰트 등록 (이미 font_utils에서 전역으로 처리)
        try:
            register_korean_fonts()
            logger.info(f"✅ 한글 폰트 등록 완료 (FONT: {FONT_NAME})")
        except Exception as e:
            logger.warning(f"⚠️ 폰트 등록 실패, Helvetica로 폴백: {e}")

    async def generate(self, date_ref: str | None = None) -> Path | None:
        start_time = time.time()
        if date_ref is None:
            date_ref = datetime.now().strftime("%Y-%m-%d")

        logger.info(f"📄 [v6.1] 주간 PDF 보고서 생성 시작 (기준일: {date_ref})")

        try:
            await self.db.init_db()
            weekly_data = await self._collect_weekly_data(date_ref)

            if weekly_data["total_decisions"] == 0:
                logger.info("⚠️ 금주 신호 없음 → '관망' 페이지 포함하여 PDF 생성")

            try:
                news_items, _ = await self.news.get_news_with_sentiment("코스피", limit=5)
                weekly_data["headlines"] = [item.get("title", "") for item in news_items[:5]]
            except Exception as e:
                logger.warning(f"뉴스 수집 실패: {e}")
                weekly_data["headlines"] = ["뉴스 데이터를 불러올 수 없습니다."]

            exits = [d for d in weekly_data["decisions"] if d.get("action") == "EXIT"]
            weekly_data["exit_count"] = len(exits)
            weekly_data["exit_avg_pnl"] = statistics.mean([d.get("pnl", 0.0) for d in exits]) if exits else 0.0

            ml_scores = [d.get("ml_score", 0.5) for d in weekly_data["decisions"] if d.get("ml_score") is not None]
            risk_adjs = [
                d.get("risk_adjustment_factor", 1.0)
                for d in weekly_data["decisions"]
                if d.get("risk_adjustment_factor") is not None
            ]
            weekly_data["avg_ml"] = statistics.mean(ml_scores) if ml_scores else 0.5
            weekly_data["avg_risk_adj"] = statistics.mean(risk_adjs) if risk_adjs else 1.0
            weekly_data["ml_high"] = max(ml_scores) if ml_scores else 0.5
            weekly_data["ml_low"] = min(ml_scores) if ml_scores else 0.5

            if self.kiwoom and not self.kiwoom.is_connected():
                try:
                    await self.kiwoom.connect()
                    if not self.kiwoom.is_connected():
                        self.kiwoom = None
                except:
                    self.kiwoom = None

            weekly_data = await self._enrich_stock_data(weekly_data)

            filename = f"Weekly_Report_{date_ref}.pdf"
            filepath = PDF_DIR / filename

            doc = SimpleDocTemplate(
                str(filepath),
                pagesize=A4,
                leftMargin=15 * mm,
                rightMargin=15 * mm,
                topMargin=20 * mm,
                bottomMargin=20 * mm,
                title=f"Quant Weekly - {date_ref}",
                author="v6.1 Quant System",
            )

            self.story = []
            self._build_styles()

            self._build_title_page(date_ref, weekly_data)
            self._build_executive_summary(weekly_data)

            if weekly_data["total_decisions"] == 0:
                self._build_no_signal_page()
            else:
                self._build_market_review(weekly_data)
                self._build_financial_health(weekly_data)
                self._build_supply_demand(weekly_data)
                self._build_ml_insights(weekly_data)

            self._build_trailing_stop_stats(weekly_data)
            self._build_news_summary(weekly_data)
            self._build_factor_attribution(weekly_data)
            self._build_portfolio_positioning(weekly_data)
            self._build_risk_analysis(weekly_data)
            self._build_scenario_analysis(weekly_data)
            self._build_appendix(weekly_data)

            doc.build(self.story)
            elapsed = time.time() - start_time
            logger.info(f"✅ PDF 생성 완료: {filepath} (소요 {elapsed:.2f}초)")
            return filepath

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ PDF 생성 실패 (소요 {elapsed:.2f}초): {e}")
            logger.error(traceback.format_exc())
            return None

    # ============================================================
    # 데이터 수집 (기존 v6.0 유지)
    # ============================================================
    async def _collect_weekly_data(self, date_ref: str) -> dict:
        end_date = datetime.strptime(date_ref, "%Y-%m-%d")
        decisions = []
        for i in range(7):
            day = (end_date - timedelta(days=i)).strftime("%Y-%m-%d")
            decisions.extend(await self.db.get_decisions_by_date(day))

        total = len(decisions)
        buy_count = sum(1 for d in decisions if d["action"] == "BUY")
        sell_count = sum(1 for d in decisions if d["action"] == "SELL")
        hold_count = sum(1 for d in decisions if d["action"] == "HOLD")
        scores = [d["score"] for d in decisions] if decisions else []
        avg_score = sum(scores) / len(scores) if scores else 0.0

        top_buy = sorted([d for d in decisions if d["action"] == "BUY"], key=lambda x: x["score"], reverse=True)[:5]
        weights = await self.db.get_weights()

        daily_counts = {}
        for d in decisions:
            raw_created = d.get("created_at", "") or ""
            day = raw_created[:10] if len(raw_created) >= 10 else ""
            if day:
                daily_counts[day] = daily_counts.get(day, 0) + 1

        return {
            "date_ref": date_ref,
            "total_decisions": total,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "hold_count": hold_count,
            "avg_score": avg_score,
            "top_buy": top_buy,
            "weights": weights,
            "daily_counts": daily_counts,
            "decisions": decisions,
            "avg_ml": 0.5,
            "avg_risk_adj": 1.0,
            "ml_high": 0.5,
            "ml_low": 0.5,
        }

    async def _enrich_stock_data(self, data: dict) -> dict:
        years_to_try = ["2024", "2023", "2022"]
        for stock in data["top_buy"]:
            ticker = stock.get("ticker", "")
            financials = {}
            supply = {}
            if self.dart:
                corp_code = self.dart.get_corp_code_sync(ticker)
                if corp_code:
                    for year in years_to_try:
                        try:
                            fin = self.dart.get_financials_sync(corp_code, year)
                            if fin and len(fin) > 0:
                                financials = fin
                                break
                        except Exception as e:
                            logger.warning(f"⚠️ {ticker} {year}년 재무 조회 실패: {e}")
                            continue
                if financials:
                    stock["financials"] = financials
            if self.kiwoom and self.kiwoom.is_connected():
                try:
                    foreign = await self.kiwoom.request_tr(ticker, "외국인수급")
                    if foreign and isinstance(foreign, dict) and "net_buy" in foreign:
                        supply["foreign_net_buy"] = foreign.get("net_buy", 0)
                except Exception as e:
                    logger.debug(f"ℹ️ {ticker} 외국인 수급 스킵: {e}")
                try:
                    inst = await self.kiwoom.request_tr(ticker, "기관수급")
                    if inst and isinstance(inst, dict) and "net_buy" in inst:
                        supply["inst_net_buy"] = inst.get("net_buy", 0)
                except Exception as e:
                    logger.debug(f"ℹ️ {ticker} 기관 수급 스킵: {e}")
                if supply:
                    stock["supply"] = supply
        return data

    # ============================================================
    # 스타일 빌드 (v6.1 - 폰트 폴백 강화)
    # ============================================================
    def _build_styles(self):
        self.styles = getSampleStyleSheet()
        # FONT_NAME이 'Helvetica'이면 폴백 경고 (font_utils에서 처리)
        if FONT_NAME == "Helvetica":
            logger.warning("⚠️ 한글 폰트 없음 → Helvetica 사용 (한글 깨짐 가능)")

        self.styles.add(
            ParagraphStyle(
                name="Title1",
                parent=self.styles["Title"],
                fontName=FONT_BOLD,
                fontSize=22,
                spaceAfter=12 * mm,
                alignment=TA_CENTER,
                textColor=colors.darkblue,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="SectionTitle",
                parent=self.styles["Heading1"],
                fontName=FONT_BOLD,
                fontSize=16,
                spaceAfter=8 * mm,
                spaceBefore=6 * mm,
                textColor=colors.darkblue,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="SubSectionTitle",
                parent=self.styles["Heading2"],
                fontName=FONT_BOLD,
                fontSize=13,
                spaceAfter=4 * mm,
                spaceBefore=3 * mm,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="BodyText",
                parent=self.styles["Normal"],
                fontName=FONT_NAME,
                fontSize=10,
                leading=14,
                alignment=TA_JUSTIFY,
                spaceAfter=3 * mm,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="SmallText", parent=self.styles["Normal"], fontName=FONT_NAME, fontSize=8, textColor=colors.grey
            )
        )

    # ============================================================
    # 각 섹션 빌드 (v6.1 - 데이터 부족 방어 강화, 성능 로깅)
    # ============================================================
    def _build_title_page(self, date_ref, data):
        self.story.append(Spacer(1, 30 * mm))
        self.story.append(Paragraph("<b>퀀트 전략 주간 리포트 v6.1</b>", self.styles["Title1"]))
        self.story.append(Spacer(1, 5 * mm))
        self.story.append(Paragraph(f"<font size=14>{date_ref}</font>", self.styles["BodyText"]))
        self.story.append(Spacer(1, 30 * mm))
        self.story.append(Paragraph(f"<b>시그널 건수</b>: {data['total_decisions']}건", self.styles["BodyText"]))
        self.story.append(
            Paragraph(f"<b>매수/매도</b>: {data['buy_count']} / {data['sell_count']}", self.styles["BodyText"])
        )
        self.story.append(Paragraph(f"<b>ML 평균 예측</b>: {data['avg_ml']:.1%}", self.styles["BodyText"]))
        self.story.append(Paragraph(f"<b>VaR 조정 계수</b>: {data['avg_risk_adj']:.2f}", self.styles["BodyText"]))
        self.story.append(Spacer(1, 20 * mm))
        self.story.append(
            Paragraph(
                "<i>본 보고서는 투자자문이 아니며, 투자 결정과 책임은 투자자 본인에게 있습니다.</i>",
                self.styles["SmallText"],
            )
        )
        self.story.append(PageBreak())

    def _build_executive_summary(self, data):
        self.story.append(Paragraph("1. Executive Summary", self.styles["SectionTitle"]))
        total, buy, sell, hold = data["total_decisions"], data["buy_count"], data["sell_count"], data["hold_count"]
        avg = data["avg_score"]
        self.story.append(
            Paragraph(
                f"금주 총 {total}개의 시그널이 발생했습니다. 매수 {buy}건, 매도 {sell}건, 관망 {hold}건으로, "
                f"매수 우위 국면이 지속되었으나 강도는 {avg*100:.0f}% 수준입니다.",
                self.styles["BodyText"],
            )
        )
        self.story.append(
            Paragraph(
                f"<b>ML 인사이트</b>: 평균 예측 확률 {data['avg_ml']:.1%} (최고 {data['ml_high']:.1%}, 최저 {data['ml_low']:.1%}), "
                f"VaR 조정 계수 평균 {data['avg_risk_adj']:.2f}",
                self.styles["BodyText"],
            )
        )
        if data["top_buy"]:
            self.story.append(Paragraph("<b>Top 3 추천 종목</b>", self.styles["SubSectionTitle"]))
            for i, d in enumerate(data["top_buy"][:3], 1):
                name = d.get("name", d.get("ticker", ""))
                score = d.get("score", 0.0)
                ml = d.get("ml_score", 0.5)
                self.story.append(
                    Paragraph(f"{i}. <b>{name}</b> — 확신도: {score:.1%} | ML 예측: {ml:.1%}", self.styles["BodyText"])
                )
        self.story.append(PageBreak())

    def _build_no_signal_page(self):
        self.story.append(Paragraph("2. 이번 주 시그널 현황", self.styles["SectionTitle"]))
        self.story.append(Paragraph("<b>⚠️ 금주 발생한 매수/매도 시그널이 없습니다.</b>", self.styles["BodyText"]))
        self.story.append(Paragraph("시장은 횡보 또는 혼조 국면으로 판단됩니다.", self.styles["BodyText"]))
        self.story.append(PageBreak())

    def _build_market_review(self, data):
        self.story.append(Paragraph("2. 주간 시장 리뷰", self.styles["SectionTitle"]))
        if data["daily_counts"]:
            table_data = [["일자", "시그널 건수"]]
            for day, count in sorted(data["daily_counts"].items()):
                table_data.append([day, str(count)])
            t = Table(table_data, colWidths=[80, 80])
            t.setStyle(
                TableStyle(
                    [("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]
                )
            )
            self.story.append(t)
        else:
            self.story.append(Paragraph("<i>일자별 데이터 부족</i>", self.styles["BodyText"]))
        self.story.append(PageBreak())

    def _build_financial_health(self, data):
        self.story.append(Paragraph("3. 종합 펀더멘털 & 수급 분석", self.styles["SectionTitle"]))
        top_buy = data["top_buy"]
        if not top_buy:
            self.story.append(Paragraph("<i>분석 가능한 종목이 없습니다.</i>", self.styles["BodyText"]))
            self.story.append(PageBreak())
            return
        table_data = [["종목", "매출(조)", "영업익(조)", "영업익률", "ROE", "부채비율", "외국인", "기관"]]
        for stock in top_buy[:5]:
            name = stock.get("name", stock.get("ticker", ""))
            fin = stock.get("financials", {})
            supply = stock.get("supply", {})
            revenue = fin.get("매출액", 0) / 1e12
            op = fin.get("영업이익", 0) / 1e12
            op_margin = fin.get("영업이익률", 0)
            roe = fin.get("ROE", 0)
            debt_ratio = fin.get("부채비율", 0)
            foreign_net = supply.get("foreign_net_buy", 0) / 1e8
            inst_net = supply.get("inst_net_buy", 0) / 1e8
            row = [
                name,
                f"{revenue:.2f}" if revenue > 0 else "-",
                f"{op:.2f}" if op > 0 else "-",
                f"{op_margin:.1f}%" if op_margin > 0 else "-",
                f"{roe:.1f}%" if roe > 0 else "-",
                f"{debt_ratio:.1f}%" if debt_ratio > 0 else "-",
                f"{foreign_net:+.0f}" if foreign_net != 0 else "-",
                f"{inst_net:+.0f}" if inst_net != 0 else "-",
            ]
            table_data.append(row)
        if len(table_data) > 1:
            t = Table(table_data, colWidths=[50, 40, 40, 35, 35, 40, 45, 45])
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.darkblue),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ]
                )
            )
            self.story.append(t)
        self.story.append(PageBreak())

    def _build_supply_demand(self, data):
        self.story.append(Paragraph("4. 수급 인사이트", self.styles["SectionTitle"]))
        top_buy = data["top_buy"]
        insights = []
        for stock in top_buy[:5]:
            name = stock.get("name", stock.get("ticker", ""))
            supply = stock.get("supply", {})
            foreign = supply.get("foreign_net_buy", 0)
            inst = supply.get("inst_net_buy", 0)
            if foreign > 0:
                insights.append(f"• {name}: 외국인 {foreign/1e8:+.0f}억 순매수")
            if inst > 0:
                insights.append(f"• {name}: 기관 {inst/1e8:+.0f}억 순매수")
        if insights:
            self.story.append(Paragraph("<b>📈 외국인/기관 자금 흐름</b>", self.styles["SubSectionTitle"]))
            for ins in insights[:5]:
                self.story.append(Paragraph(ins, self.styles["BodyText"]))
        else:
            self.story.append(Paragraph("<i>수급 데이터 없음</i>", self.styles["BodyText"]))
        self.story.append(PageBreak())

    def _build_ml_insights(self, data):
        self.story.append(Paragraph("🧠 ML 예측 인사이트", self.styles["SectionTitle"]))
        self.story.append(
            Paragraph(
                f"• 평균 ML 예측 확률: <b>{data['avg_ml']:.1%}</b> (범위: {data['ml_low']:.1%} ~ {data['ml_high']:.1%})",
                self.styles["BodyText"],
            )
        )
        self.story.append(
            Paragraph(
                f"• VaR 조정 계수 평균: <b>{data['avg_risk_adj']:.2f}</b> (1.0=기준, 낮을수록 리스크 축소)",
                self.styles["BodyText"],
            )
        )
        sorted_by_ml = sorted(
            [d for d in data["decisions"] if d.get("ml_score") is not None],
            key=lambda x: x.get("ml_score", 0),
            reverse=True,
        )[:3]
        if sorted_by_ml:
            self.story.append(Paragraph("<b>ML 최고 예측 종목</b>", self.styles["SubSectionTitle"]))
            for d in sorted_by_ml:
                name = d.get("name", d.get("ticker", ""))
                ml = d.get("ml_score", 0.5)
                action = d.get("action", "HOLD")
                self.story.append(Paragraph(f"• {name} — ML {ml:.1%} | 액션: {action}", self.styles["BodyText"]))
        self.story.append(PageBreak())

    def _build_trailing_stop_stats(self, data):
        self.story.append(Paragraph("📉 트레일링 스탑 성과", self.styles["SectionTitle"]))
        exit_cnt = data.get("exit_count", 0)
        avg_pnl = data.get("exit_avg_pnl", 0.0)
        self.story.append(Paragraph(f"• 이번 주 트레일링 스탑 청산 건수: <b>{exit_cnt}건</b>", self.styles["BodyText"]))
        self.story.append(Paragraph(f"• 청산 평균 손익률: <b>{avg_pnl:+.2f}%</b>", self.styles["BodyText"]))
        if exit_cnt == 0:
            self.story.append(
                Paragraph("<i>※ 이번 주는 트레일링 스탑이 활성화되지 않았습니다.</i>", self.styles["BodyText"])
            )
        self.story.append(PageBreak())

    def _build_news_summary(self, data):
        self.story.append(Paragraph("📰 이번주 주요 헤드라인", self.styles["SectionTitle"]))
        headlines = data.get("headlines", [])
        if headlines and headlines[0] != "뉴스 데이터를 불러올 수 없습니다.":
            for i, h in enumerate(headlines[:5], 1):
                self.story.append(Paragraph(f"{i}. {h}", self.styles["BodyText"]))
        else:
            self.story.append(Paragraph("<i>수집된 뉴스가 없습니다.</i>", self.styles["BodyText"]))
        self.story.append(PageBreak())

    def _build_factor_attribution(self, data):
        self.story.append(Paragraph("5. 팩터 귀속 분석", self.styles["SectionTitle"]))
        weights = data.get("weights", {})
        if weights:
            for f, w in weights.items():
                bar = "█" * int(w * 20) + "░" * (20 - int(w * 20))
                self.story.append(Paragraph(f"• {f:<12}: {bar} {w:.2f}", self.styles["BodyText"]))
        else:
            self.story.append(Paragraph("<i>초기 가중치 (학습 전)</i>", self.styles["BodyText"]))
        self.story.append(PageBreak())

    def _build_portfolio_positioning(self, data):
        self.story.append(Paragraph("6. 포트폴리오 포지셔닝", self.styles["SectionTitle"]))
        avg = data.get("avg_score", 0.5)
        avg_ml = data.get("avg_ml", 0.5)
        core = min(80, int(45 + avg * 30 + avg_ml * 20))
        tactical = max(5, int(30 - avg * 15 - avg_ml * 10))
        cash = 100 - core - tactical - 5
        self.story.append(Paragraph(f"• Core (반도체·인프라): <b>{core}%</b>", self.styles["BodyText"]))
        self.story.append(Paragraph(f"• Tactical (로봇·피지컬AI): <b>{tactical}%</b>", self.styles["BodyText"]))
        self.story.append(Paragraph("• Optionality (양자·보안): <b>5%</b>", self.styles["BodyText"]))
        self.story.append(
            Paragraph(f"• 현금: <b>{cash}%</b> (평균점수 {avg:.0%} + ML {avg_ml:.0%})", self.styles["BodyText"])
        )
        self.story.append(PageBreak())

    def _build_risk_analysis(self, data):
        self.story.append(Paragraph("7. 위험 분석", self.styles["SectionTitle"]))
        risks = [
            ("전력 병목", "데이터센터 전력 인허가 지연", "중간"),
            ("HBM 가격", "피크아웃 조기화 가능성", "중간"),
            ("로봇 서사", "파일럿→수주 전환 부재", "높음"),
        ]
        table_data = [["리스크", "설명", "수준"]] + risks
        t = Table(table_data, colWidths=[50, 120, 40])
        t.setStyle(
            TableStyle(
                [("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey), ("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]
            )
        )
        self.story.append(t)
        self.story.append(PageBreak())

    def _build_scenario_analysis(self, data):
        self.story.append(Paragraph("8. 시나리오 분석", self.styles["SectionTitle"]))
        scenarios = [
            ("낙관 (25%)", "추론 수요 폭증", "Core 확대"),
            ("기준 (55%)", "반도체 강세 지속", "Core 60~70%"),
            ("비관 (20%)", "CapEx 피크아웃", "비중 축소"),
        ]
        for name, desc, action in scenarios:
            self.story.append(Paragraph(f"<b>{name}</b>", self.styles["SubSectionTitle"]))
            self.story.append(Paragraph(f"• {desc} → {action}", self.styles["BodyText"]))
        self.story.append(PageBreak())

    def _build_appendix(self, data):
        self.story.append(Paragraph("부록", self.styles["SectionTitle"]))
        self.story.append(Paragraph("<b>데이터 출처</b>", self.styles["SubSectionTitle"]))
        self.story.append(Paragraph("• 실시간 시세: Kiwoom WebSocket", self.styles["BodyText"]))
        self.story.append(Paragraph("• 재무제표: DART Open API", self.styles["BodyText"]))
        self.story.append(Paragraph("• 수급 데이터: Kiwoom REST", self.styles["BodyText"]))
        self.story.append(Paragraph("• 뉴스: NewsCrawler (NAVER API HUB)", self.styles["BodyText"]))
        self.story.append(
            Paragraph(f"• ML 엔진: XGBoost (평균 예측 {data.get('avg_ml', 0.5):.1%})", self.styles["BodyText"])
        )
        self.story.append(Paragraph(f"• VaR 조정 계수: {data.get('avg_risk_adj', 1.0):.2f}", self.styles["BodyText"]))
