"""
core/font_utils.py - v1.0 (ReportLab 한글 폰트 통합 유틸리티)
- daily_report.py와 weekly_pdf.py의 중복 폰트 등록 코드를 통합
"""

from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from core.logger import setup_logger

logger = setup_logger("font_utils")

FONT_NAME = 'Helvetica'
FONT_BOLD = 'Helvetica-Bold'

def register_korean_fonts():
    """한글 폰트를 등록하고 전역 변수 FONT_NAME, FONT_BOLD 설정"""
    global FONT_NAME, FONT_BOLD
    try:
        pdfmetrics.registerFont(TTFont('MalgunGothic', 'C:/Windows/Fonts/malgun.ttf'))
        pdfmetrics.registerFont(TTFont('MalgunGothic-Bold', 'C:/Windows/Fonts/malgunbd.ttf'))
        FONT_NAME = 'MalgunGothic'
        FONT_BOLD = 'MalgunGothic-Bold'
        logger.info("✅ MalgunGothic 폰트 등록 완료")
        return
    except:
        pass

    try:
        font_path = Path(__file__).parent.parent / "fonts" / "NanumGothic.ttf"
        if font_path.exists():
            pdfmetrics.registerFont(TTFont('NanumGothic', str(font_path)))
            FONT_NAME = 'NanumGothic'
            FONT_BOLD = 'NanumGothic-Bold'
            logger.info("✅ NanumGothic 폰트 등록 완료")
            return
    except:
        pass

    logger.warning("⚠️ 한글 폰트 없음 → Helvetica 사용 (한글 깨짐 가능)")
    FONT_NAME = 'Helvetica'
    FONT_BOLD = 'Helvetica-Bold'

# 모듈 임포트 시 자동 실행
register_korean_fonts()