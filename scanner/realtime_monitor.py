"""
Realtime Monitor v5.1.2
Tier 기반 실시간 감시 (Push 기반)
"""

import asyncio
from typing import Dict, List, Optional
from datetime import datetime

from core.logger import setup_logger
from data.kiwoom_connector import KiwoomConnectorV512
from data.stock_universe import StockUniverse

logger = setup_logger("monitor")


class RealtimeMonitor:
    """실시간 감시기 (Tier 기반 Push)"""
    
    def __init__(self, kiwoom: KiwoomConnectorV512):
        self.kiwoom = kiwoom
        self.universe = StockUniverse()
        self.detected: List[Dict] = []
        
        # Tier 설정
        self.tier1_stocks = self.universe.get_tier1(50)
        self.tier2_stocks = self.universe.get_tier2(400)
        self.tier3_stocks = self.universe.get_tier3()
    
    async def start(self):
        """실시간 감시 시작 (Push 등록)"""
        logger.info("RealtimeMonitor starting...")
        
        # Tier1: 실시간 Push 등록
        for stock in self.tier1_stocks:
            await self.kiwoom.register_realtime(stock.code, self._on_realtime)
        
        logger.info(f"Registered {len(self.tier1_stocks)} realtime subscriptions")
    
    def _on_realtime(self, data: Dict):
        """실시간 데이터 수신 콜백"""
        ticker = data.get("ticker")
        if not ticker:
            return
        
        # 이상 징후 감지
        anomalies = self._detect_anomalies(data)
        if anomalies:
            self.detected.append({
                "ticker": ticker,
                "timestamp": datetime.now().isoformat(),
                "data": data,
                "anomalies": anomalies
            })
            logger.info(f"Anomaly detected: {ticker} ({', '.join(anomalies)})")
    
    def _detect_anomalies(self, data: Dict) -> List[str]:
        """이상 징후 감지"""
        anomalies = []
        
        # 1. 가격 변동 (KOSPI/KOSDAQ 기준)
        change = data.get("change", 0)
        if abs(change) > 3.0:
            anomalies.append("급등" if change > 0 else "급락")
        
        # 2. 거래량 급증
        volume_ratio = data.get("volume_ratio", 1.0)
        if volume_ratio > 3.0:
            anomalies.append("거래량 급증")
        
        return anomalies
    
    async def scan(self) -> List[Dict]:
        """감지된 종목 반환 (소비)"""
        detected = self.detected.copy()
        self.detected = []
        return detected