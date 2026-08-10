"""
report/weekly_pdf.py - v5.2.0 Institutional Weekly PDF Report (DART 연동)
- reportlab 기반 20~30페이지 종합 보고서
- DART API를 통해 매출, 영업이익, 자산 등 재무 데이터 포함
- 공시 검색 결과 포함
"""

import os
import io
import json
import math
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle,
    Image, KeepTogether, PageTemplate, BaseDocTemplate, Frame, NextPageTemplate
)
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart

from core.logger import setup_logger
from data.db_manager import DatabaseManager
from data.dart_connector import DartConnector
from dotenv import load_dotenv

logger = setup_logger("weekly_pdf")

# PDF 저장 경로
PDF_DIR = Path(__file__).parent.parent / "reports"
PDF_DIR.mkdir(parents=True, exist_ok=True)

# 한글 폰트 등록 (시도, 없으면 기본 폰트 사용)
try:
    pdfmetrics.registerFont(TTFont('MalgunGothic', 'C:/Windows/Fonts/malgun.ttf'))
    pdfmetrics.registerFont(TTFont('MalgunGothic-Bold', 'C:/Windows/Fonts/malgunbd.ttf'))
    FONT_NAME = 'MalgunGothic'
    FONT_BOLD = 'MalgunGothic-Bold'
except:
    try:
        font_path = Path(__file__).parent.parent / "fonts" / "NanumGothic.ttf"
        bold_path = Path(__file__).parent.parent / "fonts" / "NanumGothicBold.ttf"
        if font_path.exists():
            pdfmetrics.registerFont(TTFont('NanumGothic', str(font_path)))
            pdfmetrics.registerFont(TTFont('NanumGothic-Bold', str(bold_path)))
            FONT_NAME = 'NanumGothic'
            FONT_BOLD = 'NanumGothic-Bold'
        else:
            FONT_NAME = 'Helvetica'
            FONT_BOLD = 'Helvetica-Bold'
    except:
        FONT_NAME = 'Helvetica'
        FONT_BOLD = 'Helvetica-Bold'

logger.info(f"📄 PDF 폰트: {FONT_NAME}")


class WeeklyPDFGenerator:
    """주간 기관용 PDF 리포트 생성기 (DART 연동)"""
    
    def __init__(self, db_manager: DatabaseManager = None, kiwoom_connector=None):
        self.db = db_manager or DatabaseManager()
        self.kiwoom = kiwoom_connector
        
        load_dotenv()
        self.dart_api_key = os.getenv("DART_API_KEY")
        self.dart = DartConnector(self.dart_api_key) if self.dart_api_key else None
        if not self.dart:
            logger.warning("⚠️ DART API 키 없음 → 재무 데이터 없이 보고서 생성")
        
        self.styles = None
        self.story = []

    async def generate(self, date_ref: Optional[str] = None) -> Optional[Path]:
        """
        주간 PDF 보고서 생성
        Args:
            date_ref: 기준일 (YYYY-MM-DD), None이면 오늘
        Returns:
            생성된 PDF 파일 경로 또는 None (실패 시)
        """
        if date_ref is None:
            date_ref = datetime.now().strftime("%Y-%m-%d")
        
        logger.info(f"📄 주간 PDF 보고서 생성 시작 (기준일: {date_ref})")
        
        # 🔥 [수정] DB 테이블이 없으면 생성 (오류 방지)
        await self.db.init_db()
        
        # 1. 데이터 수집
        weekly_data = await self._collect_weekly_data(date_ref)
        if weekly_data['total_decisions'] == 0:
            logger.info("⚠️ 주간 신호 없음 → PDF 생성 스킵")
            return None
        
        # 2. DART 재무 데이터 수집 (Top BUY 종목)
        if self.dart:
            weekly_data = await self._enrich_with_financials(weekly_data)
        
        # 3. PDF 문서 생성
        filename = f"Weekly_Report_{date_ref}.pdf"
        filepath = PDF_DIR / filename
        
        doc = SimpleDocTemplate(
            str(filepath),
            pagesize=A4,
            leftMargin=15*mm,
            rightMargin=15*mm,
            topMargin=20*mm,
            bottomMargin=20*mm,
            title=f"Quant Weekly - {date_ref}",
            author="v5.2.0 Quant System"
        )
        
        self.story = []
        self._build_styles()
        
        # 4. 각 섹션 빌드
        self._build_title_page(date_ref, weekly_data)
        self._build_executive_summary(weekly_data)
        self._build_market_review(weekly_data)
        self._build_financial_health(weekly_data)
        self._build_sector_performance(weekly_data)
        self._build_factor_attribution(weekly_data)
        self._build_portfolio_positioning(weekly_data)
        self._build_risk_analysis(weekly_data)
        self._build_scenario_analysis(weekly_data)
        self._build_appendix(weekly_data)
        
        # 5. PDF 빌드
        doc.build(self.story)
        logger.info(f"✅ PDF 생성 완료: {filepath}")
        return filepath

    # ============================================================
    # 1. 데이터 수집 (변경 없음)
    # ============================================================
    async def _collect_weekly_data(self, date_ref: str) -> Dict:
        """주간 리포트용 데이터 수집 (7일치)"""
        end_date = datetime.strptime(date_ref, "%Y-%m-%d")
        start_date = end_date - timedelta(days=7)
        
        decisions = []
        for i in range(7):
            day = (end_date - timedelta(days=i)).strftime("%Y-%m-%d")
            day_decisions = await self.db.get_decisions_by_date(day)
            decisions.extend(day_decisions)
        
        total = len(decisions)
        buy_count = sum(1 for d in decisions if d['action'] == 'BUY')
        sell_count = sum(1 for d in decisions if d['action'] == 'SELL')
        hold_count = sum(1 for d in decisions if d['action'] == 'HOLD')
        scores = [d['score'] for d in decisions] if decisions else []
        avg_score = sum(scores) / len(scores) if scores else 0.0
        
        top_buy = sorted([d for d in decisions if d['action'] == 'BUY'], 
                        key=lambda x: x['score'], reverse=True)[:5]
        
        weights = await self.db.get_weights()
        
        daily_counts = {}
        for d in decisions:
            day = d.get('created_at', '').split('T')[0] if 'created_at' in d else ''
            if day:
                daily_counts[day] = daily_counts.get(day, 0) + 1
        
        return {
            'date_ref': date_ref,
            'start_date': start_date.strftime("%Y-%m-%d"),
            'total_decisions': total,
            'buy_count': buy_count,
            'sell_count': sell_count,
            'hold_count': hold_count,
            'avg_score': avg_score,
            'top_buy': top_buy,
            'weights': weights,
            'daily_counts': daily_counts,
            'decisions': decisions
        }

    async def _enrich_with_financials(self, data: Dict) -> Dict:
        """Top BUY 종목에 DART 재무 데이터 추가 (변경 없음)"""
        if not self.dart:
            return data
        
        code_map = {
            "005930": "00126380",
            "000660": "00126379",
            "035420": "00126381",
            "005380": "00126382",
        }
        
        for stock in data['top_buy']:
            ticker = stock.get('ticker', '')
            corp_code = code_map.get(ticker)
            if not corp_code:
                continue
            
            fs = self.dart.get_financials_sync(corp_code, "2024")
            if fs and fs.get('status') == '000':
                financials = {}
                for item in fs.get('list', []):
                    if item.get('sj_div') != 'CFS':
                        continue
                    account = item.get('account_nm')
                    raw_amount = item.get('thstrm_amount', '0')
                    try:
                        amount = float(raw_amount.replace(',', ''))
                    except:
                        amount = None
                    if account in ['매출액', '영업이익', '당기순이익', '자산총계', '부채총계']:
                        financials[account] = amount
                stock['financials'] = financials
            
            notices = self.dart.search_notices_sync(corp_code, "20250101", 1)
            if notices and len(notices) > 0:
                stock['latest_notice'] = notices[0].get('report_nm', 'N/A')
        
        return data

    # ============================================================
    # 2. 스타일 빌드 (변경 없음)
    # ============================================================
    def _build_styles(self):
        self.styles = getSampleStyleSheet()
        
        self.styles.add(ParagraphStyle(
            name='Title1',
            parent=self.styles['Title'],
            fontName=FONT_BOLD,
            fontSize=22,
            spaceAfter=12*mm,
            alignment=TA_CENTER,
            textColor=colors.darkblue
        ))
        
        self.styles.add(ParagraphStyle(
            name='SectionTitle',
            parent=self.styles['Heading1'],
            fontName=FONT_BOLD,
            fontSize=16,
            spaceAfter=8*mm,
            spaceBefore=6*mm,
            textColor=colors.darkblue,
            borderPadding=3,
            borderWidth=1,
            borderColor=colors.lightgrey
        ))
        
        self.styles.add(ParagraphStyle(
            name='SubSectionTitle',
            parent=self.styles['Heading2'],
            fontName=FONT_BOLD,
            fontSize=13,
            spaceAfter=4*mm,
            spaceBefore=3*mm,
            textColor=colors.darkblue
        ))
        
        self.styles.add(ParagraphStyle(
            name='BodyText',
            parent=self.styles['Normal'],
            fontName=FONT_NAME,
            fontSize=10,
            leading=14,
            alignment=TA_JUSTIFY,
            spaceAfter=3*mm
        ))
        
        self.styles.add(ParagraphStyle(
            name='SmallText',
            parent=self.styles['Normal'],
            fontName=FONT_NAME,
            fontSize=8,
            textColor=colors.grey
        ))

    # ============================================================
    # 3. 각 섹션 빌드 (변경 없음, 생략)
    # ============================================================
    # ... (나머지 메서드는 동일하므로 생략 - 이전 코드 유지)