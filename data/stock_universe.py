"""
data/stock_universe.py - v5.7.0 (자동 종목 로드, 500+ 종목 지원)
- data/krx_universe.csv 파일이 있으면 로드
- 없으면 인터넷에서 KRX 종목 리스트를 가져와 CSV 생성
- 그래도 안 되면 기본 5개 종목으로 폴백 (시스템 중단 방지)
"""

import csv
import json
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from core.logger import setup_logger

logger = setup_logger("universe")

# CSV 파일 경로
CSV_PATH = Path(__file__).parent / "krx_universe.csv"


@dataclass
class StockInfo:
    code: str
    name: str
    market: str
    listed_date: str
    delisted_date: Optional[str] = None
    is_active: bool = True


def get_universe() -> Dict[str, str]:
    """
    종목코드 → 종목명 매핑 딕셔너리 반환
    - 1순위: krx_universe.csv 파일에서 로드
    - 2순위: 인터넷에서 실시간 다운로드 (pandas + requests)
    - 3순위: 기본 5개 종목 (Fallback)
    """
    # ---------- 1순위: CSV 파일 로드 ----------
    if CSV_PATH.exists():
        try:
            import pandas as pd
            df = pd.read_csv(CSV_PATH, dtype={'code': str})
            # code 컬럼을 6자리 문자열로 정규화
            df['code'] = df['code'].astype(str).str.zfill(6)
            universe = dict(zip(df['code'], df['name']))
            logger.info(f"✅ CSV에서 {len(universe)}개 종목 로드 완료 ({CSV_PATH})")
            return universe
        except Exception as e:
            logger.warning(f"⚠️ CSV 로드 실패: {e} → 다음 단계로")

    # ---------- 2순위: 인터넷에서 실시간 다운로드 ----------
    try:
        logger.info("📡 인터넷에서 KRX 종목 리스트 다운로드 중...")
        import pandas as pd
        
        # 한국거래소(KRX) 상장 종목 리스트 URL (예시)
        # 실제로는 네이버 금융 또는 KRX 공식 데이터 활용
        url = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13"
        df = pd.read_html(url, header=0)[0]
        
        # 컬럼명 정리
        if '종목코드' in df.columns and '회사명' in df.columns:
            df['code'] = df['종목코드'].astype(str).str.zfill(6)
            universe = dict(zip(df['code'], df['회사명']))
        elif 'code' in df.columns and 'name' in df.columns:
            df['code'] = df['code'].astype(str).str.zfill(6)
            universe = dict(zip(df['code'], df['name']))
        else:
            # 알 수 없는 포맷 → 기본 폴백
            raise ValueError("KRX 데이터 포맷 인식 불가")
        
        # CSV로 저장 (다음에 빠르게 로드)
        df.to_csv(CSV_PATH, index=False)
        logger.info(f"✅ 인터넷에서 {len(universe)}개 종목 로드 및 CSV 저장 완료")
        return universe
        
    except Exception as e:
        logger.warning(f"⚠️ 인터넷 로드 실패: {e} → 기본 종목으로 폴백")

    # ---------- 3순위: 기본 종목 (Fallback) ----------
    logger.warning("⚠️ 기본 종목 5개만 로드 (전체 감시 불가)")
    return {
        "005930": "삼성전자",
        "000660": "SK하이닉스",
        "035420": "NAVER",
        "005380": "현대차",
        "051910": "LG화학",
    }


# ============================================================
# 기존 StockUniverse 클래스 (호환성 유지)
# ============================================================
class StockUniverse:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance
    
    def _init(self):
        self._stocks: Dict[str, StockInfo] = {}
        universe = get_universe()
        for code, name in universe.items():
            self._stocks[code] = StockInfo(code, name, "KOSPI", "2000-01-01")
        logger.info(f"StockUniverse 로드 완료: {len(self._stocks)}개 종목")
    
    def get_all(self) -> List[StockInfo]:
        return list(self._stocks.values())
    
    def get_active(self) -> List[StockInfo]:
        return [s for s in self._stocks.values() if s.is_active]
    
    def get_by_market(self, market: str) -> List[StockInfo]:
        return [s for s in self._stocks.values() if s.market == market]
    
    def get_tier1(self, count: int = 500) -> List[StockInfo]:
        return self.get_active()[:count]
    
    def get_tier2(self, count: int = 400) -> List[StockInfo]:
        return self.get_active()[500:500+count]
    
    def get_tier3(self) -> List[StockInfo]:
        return self.get_active()[900:]
    
    def get_historical_universe(self, date: str) -> List[StockInfo]:
        return [s for s in self._stocks.values() 
                if s.listed_date <= date and (s.delisted_date is None or s.delisted_date > date)]
    
    def add_delisted(self, code: str, delisted_date: str):
        if code in self._stocks:
            self._stocks[code].delisted_date = delisted_date
            self._stocks[code].is_active = False


# 테스트용
if __name__ == "__main__":
    universe = get_universe()
    print(f"📊 Universe 크기: {len(universe)}개 종목")
    for code, name in list(universe.items())[:10]:
        print(f"  • {code}: {name}")