# -*- coding: utf-8 -*-
"""
risk/correlation_matrix.py - 실시간 상관행렬 갱신 v1.0

개요:
    종목 간 수익률 상관관계를 실시간으로 추적하고,
    포트폴리오 분산 최적화에 사용할 상관행렬을 제공한다.

주요 기능:
    - RollingCorrelation: 슬라이딩 윈도우 수익률 기반 상관행렬 계산
    - DiversificationScore: 포트폴리오 분산화 점수 (낮을수록 상관 높음)
    - 포지션 조정 권고: 상관 임계값 초과 종목쌍 감지

공식:
    Pearson 상관계수:
        r(X,Y) = Σ(xi−x̄)(yi−ȳ) / √(Σ(xi−x̄)² · Σ(yi−ȳ)²)

    포트폴리오 분산화 점수:
        D = 1 − (평균 |상관계수|) ... 1=완전분산, 0=완전상관

사용 방법:
    matrix = RollingCorrelation(window=60)
    matrix.add_return("005930", 0.012)
    matrix.add_return("000660", -0.005)
    corr = matrix.correlation("005930", "000660")
    score = matrix.diversification_score(["005930", "000660", "035720"])
"""

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.logger import setup_logger

logger = setup_logger("correlation_matrix")

# ─── 상수 ──────────────────────────────────────────────────────────
_MIN_WINDOW_CORR  = 5       # 상관계수 계산 최소 공통 데이터 수
_HIGH_CORR_WARN   = 0.80    # 고상관 경고 임계값
_DEFAULT_WINDOW   = 60      # 기본 롤링 윈도우 (거래일 수)
_MAX_TICKERS      = 50      # 최대 추적 종목 수


# ═══════════════════════════════════════════════════════════════════
#  DTO
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CorrelationPair:
    """두 종목 간 상관관계 정보.

    Attributes:
        ticker_a: 종목 A
        ticker_b: 종목 B
        correlation: Pearson 상관계수 (-1~1)
        n_samples: 계산에 사용된 공통 데이터 수
        is_high: 고상관 여부 (|r| >= 임계값)
        timestamp: 계산 시각
    """
    ticker_a: str
    ticker_b: str
    correlation: float
    n_samples: int
    is_high: bool
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker_a": self.ticker_a,
            "ticker_b": self.ticker_b,
            "correlation": round(self.correlation, 4),
            "n_samples": self.n_samples,
            "is_high": self.is_high,
            "timestamp": self.timestamp,
        }


@dataclass
class DiversificationReport:
    """포트폴리오 분산화 보고서.

    Attributes:
        score: 분산화 점수 (0~1, 높을수록 좋음)
        tickers: 평가 대상 종목 목록
        high_corr_pairs: 고상관 종목쌍 목록
        avg_abs_correlation: 평균 절대 상관계수
        recommendation: 분산화 권고 메시지
    """
    score: float
    tickers: List[str]
    high_corr_pairs: List[CorrelationPair]
    avg_abs_correlation: float
    recommendation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "tickers": self.tickers,
            "avg_abs_correlation": round(self.avg_abs_correlation, 4),
            "high_corr_pairs": [p.to_dict() for p in self.high_corr_pairs],
            "recommendation": self.recommendation,
        }


# ═══════════════════════════════════════════════════════════════════
#  순수 함수 — 상관계수 계산
# ═══════════════════════════════════════════════════════════════════

def _pearson_correlation(
    x: List[float], y: List[float]
) -> Optional[float]:
    """두 시계열의 Pearson 상관계수 계산.

    Args:
        x, y: 같은 길이의 수익률 리스트

    Returns:
        float: -1~1, 계산 불가 시 None
    """
    n = len(x)
    if n != len(y) or n < 2:
        return None

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)

    denom = math.sqrt(var_x * var_y)
    if denom < 1e-12:
        return None

    r = cov / denom
    return max(-1.0, min(1.0, r))


def _align_returns(
    returns_a: List[float],
    returns_b: List[float],
) -> Tuple[List[float], List[float]]:
    """두 수익률 시계열의 마지막 공통 구간을 추출합니다."""
    n = min(len(returns_a), len(returns_b))
    if n == 0:
        return [], []
    return list(returns_a[-n:]), list(returns_b[-n:])


# ═══════════════════════════════════════════════════════════════════
#  RollingCorrelation
# ═══════════════════════════════════════════════════════════════════

class RollingCorrelation:
    """슬라이딩 윈도우 기반 실시간 상관행렬.

    Args:
        window: 롤링 윈도우 크기 (기본 60거래일)
        high_corr_threshold: 고상관 경고 임계값 (기본 0.80)
        max_tickers: 최대 추적 종목 수
    """

    def __init__(
        self,
        window: int = _DEFAULT_WINDOW,
        high_corr_threshold: float = _HIGH_CORR_WARN,
        max_tickers: int = _MAX_TICKERS,
    ) -> None:
        if window < _MIN_WINDOW_CORR:
            raise ValueError(f"window must be >= {_MIN_WINDOW_CORR}")
        if not (0 < high_corr_threshold <= 1):
            raise ValueError("high_corr_threshold must be in (0, 1]")
        self._window = window
        self._high_corr_threshold = high_corr_threshold
        self._max_tickers = max_tickers
        # {ticker: [return, return, ...]}
        self._returns: Dict[str, List[float]] = {}

    # ── 공개 API ──────────────────────────────────────────────────

    def add_return(self, ticker: str, daily_return: float) -> None:
        """종목의 일간 수익률을 추가합니다.

        Args:
            ticker: 종목 코드
            daily_return: 일간 수익률 (예: 0.012 = +1.2%)
        """
        if ticker not in self._returns:
            if len(self._returns) >= self._max_tickers:
                logger.warning(
                    "[RollingCorrelation] max_tickers(%d) reached; ignoring %s",
                    self._max_tickers, ticker,
                )
                return
            self._returns[ticker] = []

        self._returns[ticker].append(daily_return)
        if len(self._returns[ticker]) > self._window:
            self._returns[ticker].pop(0)

    def add_returns_batch(self, returns: Dict[str, float]) -> None:
        """여러 종목의 수익률을 한 번에 추가합니다.

        Args:
            returns: {ticker: daily_return} 딕셔너리
        """
        for ticker, ret in returns.items():
            self.add_return(ticker, ret)

    def correlation(
        self,
        ticker_a: str,
        ticker_b: str,
    ) -> Optional[CorrelationPair]:
        """두 종목 간 상관계수를 계산합니다.

        Args:
            ticker_a, ticker_b: 종목 코드

        Returns:
            CorrelationPair, 데이터 부족 시 None
        """
        ra = self._returns.get(ticker_a)
        rb = self._returns.get(ticker_b)
        if ra is None or rb is None:
            return None

        ax, bx = _align_returns(ra, rb)
        if len(ax) < _MIN_WINDOW_CORR:
            return None

        r = _pearson_correlation(ax, bx)
        if r is None:
            return None

        return CorrelationPair(
            ticker_a=ticker_a,
            ticker_b=ticker_b,
            correlation=r,
            n_samples=len(ax),
            is_high=abs(r) >= self._high_corr_threshold,
        )

    def correlation_matrix(
        self,
        tickers: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, float]]:
        """지정 종목들의 상관행렬을 딕셔너리로 반환합니다.

        Args:
            tickers: 종목 목록 (None이면 전체 추적 종목)

        Returns:
            {ticker_a: {ticker_b: correlation}} 중첩 딕셔너리
        """
        if tickers is None:
            tickers = list(self._returns.keys())

        matrix: Dict[str, Dict[str, float]] = {}
        for ta in tickers:
            matrix[ta] = {}
            for tb in tickers:
                if ta == tb:
                    matrix[ta][tb] = 1.0
                else:
                    pair = self.correlation(ta, tb)
                    matrix[ta][tb] = round(pair.correlation, 4) if pair else 0.0

        return matrix

    def high_correlation_pairs(
        self,
        tickers: Optional[List[str]] = None,
    ) -> List[CorrelationPair]:
        """|상관계수| >= threshold인 종목쌍 목록 반환 (중복 없음)."""
        if tickers is None:
            tickers = list(self._returns.keys())

        seen: set = set()
        high_pairs = []
        for i, ta in enumerate(tickers):
            for tb in tickers[i + 1:]:
                key = (min(ta, tb), max(ta, tb))
                if key in seen:
                    continue
                seen.add(key)
                pair = self.correlation(ta, tb)
                if pair and pair.is_high:
                    high_pairs.append(pair)
        return high_pairs

    def diversification_score(
        self,
        tickers: Optional[List[str]] = None,
    ) -> DiversificationReport:
        """포트폴리오 분산화 점수와 권고를 반환합니다.

        Score = 1 - 평균_절대_상관계수
        1.0 = 완전 비상관 (최적), 0.0 = 완전 상관

        Args:
            tickers: 평가 대상 종목 목록

        Returns:
            DiversificationReport
        """
        if tickers is None:
            tickers = list(self._returns.keys())

        if len(tickers) < 2:
            return DiversificationReport(
                score=1.0,
                tickers=tickers,
                high_corr_pairs=[],
                avg_abs_correlation=0.0,
                recommendation="Not enough tickers to evaluate diversification",
            )

        pairs = []
        abs_corrs = []
        high_pairs = []
        for i, ta in enumerate(tickers):
            for tb in tickers[i + 1:]:
                pair = self.correlation(ta, tb)
                if pair:
                    pairs.append(pair)
                    abs_corrs.append(abs(pair.correlation))
                    if pair.is_high:
                        high_pairs.append(pair)

        if not abs_corrs:
            return DiversificationReport(
                score=1.0,
                tickers=tickers,
                high_corr_pairs=[],
                avg_abs_correlation=0.0,
                recommendation="Insufficient data to compute correlations",
            )

        avg_abs = sum(abs_corrs) / len(abs_corrs)
        score = max(0.0, min(1.0, 1.0 - avg_abs))

        if score >= 0.7:
            rec = f"Good diversification (score={score:.2f})"
        elif score >= 0.4:
            rec = (
                f"Moderate diversification (score={score:.2f}); "
                f"{len(high_pairs)} high-corr pairs detected"
            )
        else:
            tickers_to_reduce = list({p.ticker_a for p in high_pairs[:3]})
            rec = (
                f"Poor diversification (score={score:.2f}); "
                f"consider reducing exposure to: {', '.join(tickers_to_reduce)}"
            )

        return DiversificationReport(
            score=score,
            tickers=tickers,
            high_corr_pairs=high_pairs,
            avg_abs_correlation=avg_abs,
            recommendation=rec,
        )

    def remove_ticker(self, ticker: str) -> bool:
        """종목 추적을 중단합니다."""
        if ticker in self._returns:
            del self._returns[ticker]
            return True
        return False

    def clear(self) -> None:
        """모든 수익률 데이터를 초기화합니다."""
        self._returns.clear()

    @property
    def tracked_tickers(self) -> List[str]:
        """현재 추적 중인 종목 목록."""
        return list(self._returns.keys())

    @property
    def ticker_count(self) -> int:
        return len(self._returns)

    def return_count(self, ticker: str) -> int:
        """특정 종목의 누적 수익률 데이터 수."""
        return len(self._returns.get(ticker, []))
