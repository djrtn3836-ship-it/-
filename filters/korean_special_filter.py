# filters/korean_special_filter.py - v5.1.4 (mypy strict 적용 - Session 24)
import logging
from datetime import datetime, time, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class KoreanSpecialFilter:
    """한국 시장 특수 필터 (5대 규칙)"""

    PRE_OPEN_START: time = time(8, 30)
    PRE_OPEN_END: time = time(9, 0)
    CLOSING_AUCTION_START: time = time(15, 20)
    CLOSING_AUCTION_END: time = time(15, 30)
    MARKET_CLOSE: time = time(15, 30)
    VI_COOLDOWN_SEC: int = 120
    DART_BLACKOUT_SEC: int = 1800
    PRICE_LIMIT_NEAR: float = 0.25
    SIGNAL_DECAY_NEAR_LIMIT: float = 0.5
    MARKET_CLOSE_BUFFER_MIN: int = 30

    def __init__(self) -> None:
        self._vi_cooldown_until: Dict[str, datetime] = {}
        self._dart_blackout_until: Dict[str, datetime] = {}
        self._log: List[Dict[str, Any]] = []

    def check(self, data: Dict[str, Any]) -> Dict[str, Any]:
        ticker: str = str(data.get("ticker", "unknown"))
        current_time: datetime = data.get("current_time", datetime.now())

        raw_price: Any = data.get("price", 0)
        try:
            price: float = float(raw_price) if raw_price is not None else 0.0
        except (ValueError, TypeError):
            price = 0.0

        reasons: List[str] = []
        passed: bool = True
        decay: float = 1.0

        if self._is_pre_open(current_time):
            reasons.append("동시호가 구간(08:30~09:00) - 신호 차단"); passed = False
        if self._is_closing_auction(current_time):
            reasons.append("동시호가 구간(15:20~15:30) - 신호 차단"); passed = False
        if self._is_vi_active(ticker, data):
            reasons.append(f"VI 발동 중 - 신호 차단 (쿨다운 {self.VI_COOLDOWN_SEC}초)"); passed = False
        if self._is_vi_cooldown(ticker, current_time):
            reasons.append("VI 해제 후 쿨다운 중 - 신호 차단"); passed = False

        raw_upper: Any = data.get("upper_limit")
        try:
            upper_limit: float = float(raw_upper) if raw_upper is not None else price * 1.30
        except (ValueError, TypeError):
            upper_limit = price * 1.30

        raw_lower: Any = data.get("lower_limit")
        try:
            lower_limit: float = float(raw_lower) if raw_lower is not None else price * 0.70
        except (ValueError, TypeError):
            lower_limit = price * 0.70

        if price > 0 and upper_limit > 0:
            upper_ratio: float = price / upper_limit
            if upper_ratio >= (1 - self.PRICE_LIMIT_NEAR):
                decay = self.SIGNAL_DECAY_NEAR_LIMIT
                reasons.append(f"상한가 근접 ({upper_ratio:.1%}) - 신호 {decay:.0%} 감쇄")

        if price > 0 and lower_limit > 0:
            lower_ratio: float = price / lower_limit
            if lower_ratio <= (1 + self.PRICE_LIMIT_NEAR):
                decay = self.SIGNAL_DECAY_NEAR_LIMIT
                reasons.append(f"하한가 근접 ({lower_ratio:.1%}) - 신호 {decay:.0%} 감쇄")

        if self._is_dart_blackout(ticker, current_time):
            reasons.append("DART 공시 후 블랙아웃 중 (30분) - 신호 차단"); passed = False
        if self._is_near_market_close(current_time):
            reasons.append(f"장 마감 {self.MARKET_CLOSE_BUFFER_MIN}분 전 - 신규 진입 금지"); passed = False

        if reasons:
            logger.info("[%s] 한국 특수 필터 적용: %s", ticker, ", ".join(reasons))
            self._log.append({"ticker": ticker, "timestamp": current_time.isoformat(), "passed": passed, "reasons": reasons, "decay": decay})

        return {"score": 1.0 if passed else 0.0, "passed": passed, "reasons": reasons, "decay": decay}

    def _is_pre_open(self, dt: datetime) -> bool:
        return self.PRE_OPEN_START <= dt.time() < self.PRE_OPEN_END

    def _is_closing_auction(self, dt: datetime) -> bool:
        return self.CLOSING_AUCTION_START <= dt.time() < self.CLOSING_AUCTION_END

    def _is_vi_active(self, ticker: str, data: Dict[str, Any]) -> bool:
        return bool(data.get("vi_active", False))

    def _is_vi_cooldown(self, ticker: str, dt: datetime) -> bool:
        return ticker in self._vi_cooldown_until and dt < self._vi_cooldown_until[ticker]

    def _is_dart_blackout(self, ticker: str, dt: datetime) -> bool:
        return ticker in self._dart_blackout_until and dt < self._dart_blackout_until[ticker]

    def _is_near_market_close(self, dt: datetime) -> bool:
        close_buffer: timedelta = timedelta(minutes=self.MARKET_CLOSE_BUFFER_MIN)
        close_time: datetime = datetime.combine(dt.date(), self.MARKET_CLOSE)
        return dt >= (close_time - close_buffer)

    def set_vi_cooldown(self, ticker: str) -> None:
        self._vi_cooldown_until[ticker] = datetime.now() + timedelta(seconds=self.VI_COOLDOWN_SEC)

    def set_dart_blackout(self, ticker: str) -> None:
        self._dart_blackout_until[ticker] = datetime.now() + timedelta(seconds=self.DART_BLACKOUT_SEC)

    def get_log(self) -> List[Dict[str, Any]]:
        return self._log
