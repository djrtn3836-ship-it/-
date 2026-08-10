"""
Stock Universe v5.1.2
2300+ 종목 관리 (Tier 분할, 생존편향 제거 준비)
"""

import csv
import json
from pathlib import Path
from typing import List, Dict, Optional
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
            # ... 2300+ 종목
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
        # 실제 구현: 해당 시점에 존재했던 종목 목록
        return [s for s in self._stocks.values() 
                if s.listed_date <= date and (s.delisted_date is None or s.delisted_date > date)]
    
    def add_delisted(self, code: str, delisted_date: str):
        """상장폐지 종목 추가 (생존편향 제거)"""
        if code in self._stocks:
            self._stocks[code].delisted_date = delisted_date
            self._stocks[code].is_active = False
            logger.info(f"Delisted: {code} ({delisted_date})")