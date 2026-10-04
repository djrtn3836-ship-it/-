"""
core/font_utils.py - v1.0 (ReportLab 한글 폰트 통합 유틸리티)
- daily_report.py와 weekly_pdf.py의 중복 폰트 등록 코드를 통합
"""

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from core.logger import setup_logger

logger = setup_logger("font_utils")

FONT_NAME = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


def register_korean_fonts() -> None:
    """한글 폰트를 등록하고 전역 변수 FONT_NAME, FONT_BOLD 설정

    우선순위:
        1) Windows 시스템 폰트(MalgunGothic) — 설치 불필요
        2) 저장소 동봉 폰트(fonts/NanumGothic*.ttf) — Linux/macOS 서버용

    🔴 2026-10-01 수정: 기존 폴백은 `NanumGothic.ttf`만 등록하고
    FONT_BOLD="NanumGothic-Bold"를 **등록하지 않아** 굵은 글씨 사용 시
    ReportLab 오류가 발생했다(리눅스 서버에서 PDF 생성 실패).
    """
    global FONT_NAME, FONT_BOLD
    try:
        pdfmetrics.registerFont(TTFont("MalgunGothic", "C:/Windows/Fonts/malgun.ttf"))
        pdfmetrics.registerFont(TTFont("MalgunGothic-Bold", "C:/Windows/Fonts/malgunbd.ttf"))
        FONT_NAME = "MalgunGothic"
        FONT_BOLD = "MalgunGothic-Bold"
        logger.info("✅ MalgunGothic 폰트 등록 완료")
        return
    except Exception:
        pass

    try:
        base = Path(__file__).parent.parent / "fonts"
        regular = base / "NanumGothic.ttf"
        bold = base / "NanumGothicBold.ttf"
        if regular.exists():
            pdfmetrics.registerFont(TTFont("NanumGothic", str(regular)))
            # 굵은체 파일이 없으면 일반체로 대체 등록(미등록 상태 방지)
            pdfmetrics.registerFont(
                TTFont("NanumGothic-Bold", str(bold if bold.exists() else regular))
            )
            FONT_NAME = "NanumGothic"
            FONT_BOLD = "NanumGothic-Bold"
            logger.info("✅ NanumGothic 폰트 등록 완료 (regular + bold)")
            return
    except Exception as e:
        logger.warning(f"⚠️ NanumGothic 등록 실패: {e}")

    logger.warning("⚠️ 한글 폰트 없음 → Helvetica 사용 (한글 깨짐 가능)")
    FONT_NAME = "Helvetica"
    FONT_BOLD = "Helvetica-Bold"


# 모듈 임포트 시 자동 실행
register_korean_fonts()
