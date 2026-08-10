"""
Korean Special Filter v5.1.2 — Claude 피드백 반영 (5대 특수 규칙)

변경사항:
1. 동시호가 구간(08:30~09:00, 15:20~15:30) 신호 차단
2. VI 발동 중 및 해제 후 2분 쿨다운 차단
3. 상하한가 ±25% 근접 시 신호 50% 감쇄
4. DART 공시 발생 후 30분간 블랙아웃
5. 장 마감 30분 전 신규 진입 금지

모든 필터 통과 여부와 사유를 로그에 기록
"""

from datetime import datetime, time, timedelta
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class KoreanSpecialFilter:
    """한국 시장 특수 필터 (5대 규칙)"""
    
    # ===== 상수 정의 =====
    PRE_OPEN_START = time(8, 30)
    PRE_OPEN_END = time(9, 0)
    CLOSING_AUCTION_START = time(15, 20)
    CLOSING_AUCTION_END = time(15, 30)
    MARKET_CLOSE = time(15, 30)
    VI_COOLDOWN_SEC = 120  # 2분
    DART_BLACKOUT_SEC = 1800  # 30분
    PRICE_LIMIT_NEAR = 0.25  # ±25%
    SIGNAL_DECAY_NEAR_LIMIT = 0.5  # 50% 감쇄
    MARKET_CLOSE_BUFFER_MIN = 30  # 장 마감 30분 전
    
    def __init__(self):
        self._vi_cooldown_until: Dict[str, datetime] = {}
        self._dart_blackout_until: Dict[str, datetime] = {}
        self._log: List[Dict] = []
    
    def check(self, data: Dict) -> Dict:
        """
        한국 시장 특수 규칙 적용
        
        Returns:
            {
                'score': float,       # 0~1 (규칙 통과 시 1.0, 실패 시 0.0)
                'passed': bool,
                'reasons': List[str],
                'decay': float        # 0~1 (상하한가 근접 시 0.5)
            }
        """
        ticker = data.get("ticker", "unknown")
        current_time = data.get("current_time", datetime.now())
        
        reasons = []
        passed = True
        decay = 1.0
        
        # ===== 규칙 1: 동시호가 구간 차단 =====
        if self._is_pre_open(current_time):
            reasons.append("동시호가 구간(08:30~09:00) - 신호 차단")
            passed = False
        
        if self._is_closing_auction(current_time):
            reasons.append("동시호가 구간(15:20~15:30) - 신호 차단")
            passed = False
        
        # ===== 규칙 2: VI 발동/쿨다운 =====
        if self._is_vi_active(ticker, data):
            reasons.append(f"VI 발동 중 - 신호 차단 (쿨다운 {self.VI_COOLDOWN_SEC}초)")
            passed = False
        
        if self._is_vi_cooldown(ticker, current_time):
            reasons.append(f"VI 해제 후 쿨다운 중 - 신호 차단")
            passed = False
        
        # ===== 규칙 3: 상하한가 근접 =====
        price = data.get("price", 0)
        upper_limit = data.get("upper_limit", price * 1.30)
        lower_limit = data.get("lower_limit", price * 0.70)
        
        if price > 0 and upper_limit > 0:
            upper_ratio = price / upper_limit
            if upper_ratio >= (1 - self.PRICE_LIMIT_NEAR):
                decay = self.SIGNAL_DECAY_NEAR_LIMIT
                reasons.append(f"상한가 근접 ({upper_ratio:.1%}) - 신호 {decay:.0%} 감쇄")
        
        if price > 0 and lower_limit > 0:
            lower_ratio = price / lower_limit
            if lower_ratio <= (1 + self.PRICE_LIMIT_NEAR):
                decay = self.SIGNAL_DECAY_NEAR_LIMIT
                reasons.append(f"하한가 근접 ({lower_ratio:.1%}) - 신호 {decay:.0%} 감쇄")
        
        # ===== 규칙 4: DART 공시 블랙아웃 =====
        if self._is_dart_blackout(ticker, current_time):
            reasons.append(f"DART 공시 후 블랙아웃 중 (30분) - 신호 차단")
            passed = False
        
        # ===== 규칙 5: 장 마감 30분 전 신규 진입 금지 =====
        if self._is_near_market_close(current_time):
            reasons.append(f"장 마감 {self.MARKET_CLOSE_BUFFER_MIN}분 전 - 신규 진입 금지")
            passed = False
        
        # ===== 로그 기록 =====
        if reasons:
            logger.info(f"[{ticker}] 한국 특수 필터 적용: {', '.join(reasons)}")
            self._log.append({
                "ticker": ticker,
                "timestamp": current_time.isoformat(),
                "passed": passed,
                "reasons": reasons,
                "decay": decay
            })
        
        return {
            "score": 1.0 if passed else 0.0,
            "passed": passed,
            "reasons": reasons,
            "decay": decay
        }
    
    def _is_pre_open(self, dt: datetime) -> bool:
        """장전 동시호가 구간 (08:30~09:00)"""
        t = dt.time()
        return self.PRE_OPEN_START <= t < self.PRE_OPEN_END
    
    def _is_closing_auction(self, dt: datetime) -> bool:
        """장마감 동시호가 구간 (15:20~15:30)"""
        t = dt.time()
        return self.CLOSING_AUCTION_START <= t < self.CLOSING_AUCTION_END
    
    def _is_vi_active(self, ticker: str, data: Dict) -> bool:
        """VI 발동 중 여부"""
        return data.get("vi_active", False)
    
    def _is_vi_cooldown(self, ticker: str, dt: datetime) -> bool:
        """VI 해제 후 쿨다운 중 여부"""
        if ticker not in self._vi_cooldown_until:
            return False
        return dt < self._vi_cooldown_until[ticker]
    
    def _is_dart_blackout(self, ticker: str, dt: datetime) -> bool:
        """DART 공시 블랙아웃 중 여부"""
        if ticker not in self._dart_blackout_until:
            return False
        return dt < self._dart_blackout_until[ticker]
    
    def _is_near_market_close(self, dt: datetime) -> bool:
        """장 마감 30분 전 여부"""
        t = dt.time()
        close_buffer = timedelta(minutes=self.MARKET_CLOSE_BUFFER_MIN)
        close_time = datetime.combine(dt.date(), self.MARKET_CLOSE)
        return dt >= (close_time - close_buffer)
    
    def set_vi_cooldown(self, ticker: str):
        """VI 해제 시 쿨다운 설정"""
        self._vi_cooldown_until[ticker] = datetime.now() + timedelta(seconds=self.VI_COOLDOWN_SEC)
        logger.debug(f"[{ticker}] VI 쿨다운 설정 (만료: {self._vi_cooldown_until[ticker]})")
    
    def set_dart_blackout(self, ticker: str):
        """DART 공시 시 블랙아웃 설정"""
        self._dart_blackout_until[ticker] = datetime.now() + timedelta(seconds=self.DART_BLACKOUT_SEC)
        logger.debug(f"[{ticker}] DART 블랙아웃 설정 (만료: {self._dart_blackout_until[ticker]})")
    
    def get_log(self) -> List[Dict]:
        """필터 로그 반환"""
        return self._log