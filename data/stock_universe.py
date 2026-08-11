"""
Stock Universe v5.4.4 (클래스 유지 + get_universe 함수 추가)
- 기존 StockUniverse 클래스 및 Tier 분할, 생존편향 기능 100% 유지
- realtime_monitor.py 호환을 위한 get_universe() 함수 추가
"""

import csv
import json
from pathlib import Path
from typing import List, Dict, Optional, Union
from dataclasses import dataclass, field

from core.logger import setup_logger

logger = setup_logger("universe")


@dataclass
class StockInfo:
    """종목 정보 (생존편향 방지)"""
    code: str
    name: str
    market: str  # KOSPI / KOSDAQ
    listed_date: str
    delisted_date: Optional[str] = None
    is_active: bool = True


class StockUniverse:
    """전종목 리스트 관리 (Tier 분할 + 생존편향)"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance
    
    def _init(self):
        self._stocks: Dict[str, StockInfo] = {}
        self._load_universe()
    
    def _load_universe(self):
        """종목 리스트 로드 (실제 KRX 데이터 필요)"""
        # 예시 데이터 (실제로는 KRX CSV 로드)
        sample_stocks = [
            StockInfo("005930", "삼성전자", "KOSPI", "1975-06-11"),
            StockInfo("000660", "SK하이닉스", "KOSPI", "1983-01-01"),
            StockInfo("035420", "NAVER", "KOSPI", "2002-10-14"),
            StockInfo("005380", "현대차", "KOSPI", "1974-06-28"),
            StockInfo("051910", "LG화학", "KOSPI", "2001-04-02"),
            # ... 실제로는 2300+ 종목이 여기에 로드됨
        ]
        
        for stock in sample_stocks:
            self._stocks[stock.code] = stock
        
        logger.info(f"Loaded {len(self._stocks)} stocks")
    
    def get_all(self) -> List[StockInfo]:
        """전체 종목 반환"""
        return list(self._stocks.values())
    
    def get_active(self) -> List[StockInfo]:
        """현재 상장 종목만 반환"""
        return [s for s in self._stocks.values() if s.is_active]
    
    def get_by_market(self, market: str) -> List[StockInfo]:
        """시장별 종목 반환"""
        return [s for s in self._stocks.values() if s.market == market]
    
    def get_tier1(self, count: int = 50) -> List[StockInfo]:
        """Tier 1 종목 (거래대금 TOP N)"""
        # 실제 구현: 거래대금 기준 정렬
        return self.get_active()[:count]
    
    def get_tier2(self, count: int = 400) -> List[StockInfo]:
        """Tier 2 종목"""
        return self.get_active()[50:50+count]
    
    def get_tier3(self) -> List[StockInfo]:
        """Tier 3 종목 (나머지)"""
        return self.get_active()[450:]
    
    def get_historical_universe(self, date: str) -> List[StockInfo]:
        """과거 특정 시점의 종목 리스트 (생존편향 제거)"""
        return [s for s in self._stocks.values() 
                if s.listed_date <= date and (s.delisted_date is None or s.delisted_date > date)]
    
    def add_delisted(self, code: str, delisted_date: str):
        """상장폐지 종목 추가 (생존편향 제거)"""
        if code in self._stocks:
            self._stocks[code].delisted_date = delisted_date
            self._stocks[code].is_active = False
            logger.info(f"Delisted: {code} ({delisted_date})")


# ============================================================
# 🔥 [신규 추가] realtime_monitor.py 호환용 함수
# ============================================================
def get_universe() -> Dict[str, str]:
    """
    realtime_monitor.py에서 사용하는 간편 매핑 함수
    - StockUniverse 클래스를 활용하여 {코드: 이름} 딕셔너리 반환
    - 오류 시 기본 종목 반환 (시스템 중단 방지)
    """
    try:
        universe = StockUniverse()
        # 활성 종목만 필터링하여 딕셔너리로 반환
        return {stock.code: stock.name for stock in universe.get_active()}
    except Exception as e:
        logger.error(f"❌ Universe 로드 실패: {e} → 기본 종목 반환")
        # 최소한의 종목이라도 반환 (시스템 완전 중단 방지)
        return {
            "005930": "삼성전자",
            "000660": "SK하이닉스",
            "035420": "NAVER",
        }


# 테스트용 (직접 실행 시)
if __name__ == "__main__":
    universe = get_universe()
    print(f"📊 Universe 로드 완료: {len(universe)}개 종목")
    for code, name in list(universe.items())[:5]:
        print(f"  • {code}: {name}")