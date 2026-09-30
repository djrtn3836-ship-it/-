# -*- coding: utf-8 -*-
"""validation/strategy_backtest.py - 프로덕션 앙상블 전략 백테스트 어댑터.

배경:
    `validation/backtest_runner.py`의 기본 시뮬레이터는 MA교차 **데모** 규칙이다.
    본 모듈은 실제 프로덕션 전략(`domain/strategies` Trend/Reversal/Breakout)을
    일봉 OHLCV 위에서 재생(replay)해 **앙상블 진입 신호**를 만들고,
    동일한 청산 규칙(`simulate_entries`)으로 거래를 생성한다.

설계 원칙:
    - 지표는 모두 **후방 참조**(index i는 ≤ i 데이터만 사용) → 룩어헤드 없음
    - 전략은 async이므로 신호를 **1회 사전 계산**해 Walk-Forward 폴드에서 슬라이스
    - 앙상블 결합: 전략별 가중 점수 합(가중치 Trend 0.40 / Reversal 0.30 / Breakout 0.30)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.logger import setup_logger
from domain.strategies.breakout import BreakoutStrategy
from domain.strategies.reversal import ReversalStrategy
from domain.strategies.trend import TrendStrategy
from validation.backtest_runner import SimConfig, ema, rsi, simulate_entries, sma

logger = setup_logger("strategy_backtest")

WARMUP_BARS = 60  # ema60 + MACD(26+9) 을 모두 확보하는 최소 봉 수


# ═══════════════════════════════════════════════════════════════════
#  지표 (순수 Python, 후방 참조)
# ═══════════════════════════════════════════════════════════════════

def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    """MACD 라인 / 시그널 / 히스토그램."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line: List[Optional[float]] = [
        (f - s) if (f is not None and s is not None) else None for f, s in zip(ema_fast, ema_slow)
    ]

    # 시그널은 MACD 라인이 존재하는 구간에서만 EMA
    valid = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    signal_line: List[Optional[float]] = [None] * len(values)
    if len(valid) >= signal:
        seq = [v for _, v in valid]
        ema_seq = ema(seq, signal)
        for (idx, _), sv in zip(valid, ema_seq):
            signal_line[idx] = sv

    hist: List[Optional[float]] = [
        (m - s) if (m is not None and s is not None) else None for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, hist


def bollinger(
    values: Sequence[float], period: int = 20, k: float = 2.0
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    """볼린저밴드 (upper, middle, lower). 모표준편차 사용."""
    middle = sma(values, period)
    upper: List[Optional[float]] = []
    lower: List[Optional[float]] = []
    for i, m in enumerate(middle):
        if m is None:
            upper.append(None)
            lower.append(None)
            continue
        window = values[i + 1 - period : i + 1]
        variance = sum((v - m) ** 2 for v in window) / period
        sd = variance ** 0.5
        upper.append(m + k * sd)
        lower.append(m - k * sd)
    return upper, middle, lower


def stochastic(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    k_period: int = 14,
    d_period: int = 3,
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """Stochastic %K / %D (슬로우 %D는 %K의 SMA)."""
    k_line: List[Optional[float]] = [None] * len(closes)
    for i in range(k_period - 1, len(closes)):
        window_high = max(highs[i + 1 - k_period : i + 1])
        window_low = min(lows[i + 1 - k_period : i + 1])
        span = window_high - window_low
        k_line[i] = 50.0 if span <= 0 else (closes[i] - window_low) / span * 100.0

    valid = [(i, v) for i, v in enumerate(k_line) if v is not None]
    d_line: List[Optional[float]] = [None] * len(closes)
    if len(valid) >= d_period:
        seq = [v for _, v in valid]
        d_seq = sma(seq, d_period)
        for (idx, _), dv in zip(valid, d_seq):
            d_line[idx] = dv
    return k_line, d_line


def volume_ratio(volumes: Sequence[float], period: int = 20) -> List[Optional[float]]:
    """거래량 / 거래량 SMA(period)."""
    avg = sma(volumes, period)
    out: List[Optional[float]] = []
    for v, a in zip(volumes, avg):
        if a is None or a <= 0:
            out.append(None)
        else:
            out.append(v / a)
    return out


def rolling_high(values: Sequence[float], period: int) -> List[Optional[float]]:
    """기간 내 최고값(후방 참조)."""
    out: List[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(max(values[: i + 1]) if i >= 0 else None)
        else:
            out.append(max(values[i + 1 - period : i + 1]))
    return out


def rolling_low(values: Sequence[float], period: int) -> List[Optional[float]]:
    """기간 내 최저값(후방 참조)."""
    out: List[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(min(values[: i + 1]) if i >= 0 else None)
        else:
            out.append(min(values[i + 1 - period : i + 1]))
    return out


def atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14
) -> List[Optional[float]]:
    """Wilder ATR (Average True Range). 워밍업 구간은 None."""
    n = len(closes)
    if n == 0 or period <= 0:
        return [None] * n

    trs: List[Optional[float]] = [None] * n
    for i in range(n):
        if i == 0:
            trs[i] = highs[i] - lows[i]
            continue
        trs[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    out: List[Optional[float]] = [None] * n
    if n < period:
        return out
    prev = sum(float(trs[i] or 0.0) for i in range(period)) / period
    out[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + float(trs[i] or 0.0)) / period
        out[i] = prev
    return out


def compute_filters(bars: Sequence[Dict[str, Any]]) -> Dict[str, List[bool]]:
    """진입 필터 마스크들을 계산한다 (전부 후방 참조).

    - "none"      : 필터 없음(전부 True)
    - "ma200"     : 종가 > SMA200 (장기 상승 추세에서만 진입)
    - "atr_calm"  : ATR% ≤ 6% (과변동 구간 회피)
    - "ma200_atr" : 위 두 조건 동시
    """
    n = len(bars)
    closes = [float(b.get("close", 0.0) or 0.0) for b in bars]
    highs = [float(b.get("high", 0.0) or 0.0) for b in bars]
    lows = [float(b.get("low", 0.0) or 0.0) for b in bars]

    ma200 = sma(closes, 200)
    atr14 = atr(highs, lows, closes, 14)

    none_mask = [True] * n
    ma_mask: List[bool] = []
    calm_mask: List[bool] = []
    for i in range(n):
        ma = ma200[i]
        ma_mask.append(bool(ma is not None and closes[i] > ma))
        a = atr14[i]
        calm_mask.append(bool(a is not None and closes[i] > 0 and (a / closes[i]) <= 0.06))

    both = [m and c for m, c in zip(ma_mask, calm_mask)]
    return {"none": none_mask, "ma200": ma_mask, "atr_calm": calm_mask, "ma200_atr": both}


FILTER_NAMES: Sequence[str] = ("none", "ma200", "atr_calm", "ma200_atr")


# ═══════════════════════════════════════════════════════════════════
#  지표 세트 생성
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BarIndicators:
    """봉 하나의 지표 묶음 (전략 입력용)."""

    tech_data: Dict[str, Any]
    regime: str
    high_52w: float
    low_52w: float
    ready: bool  # 워밍업 완료 여부


def compute_indicators(bars: Sequence[Dict[str, Any]], warmup: int = WARMUP_BARS) -> List[BarIndicators]:
    """전 봉에 대해 지표를 계산한다 (index i는 ≤ i 데이터만 사용)."""
    closes = [float(b.get("close", 0.0) or 0.0) for b in bars]
    highs = [float(b.get("high", 0.0) or 0.0) for b in bars]
    lows = [float(b.get("low", 0.0) or 0.0) for b in bars]
    volumes = [float(b.get("volume", 0.0) or 0.0) for b in bars]

    ema5 = ema(closes, 5)
    ema20 = ema(closes, 20)
    ema60 = ema(closes, 60)
    rsi14 = rsi(closes, 14)
    macd_line, macd_sig, macd_hist = macd(closes)
    bb_up, bb_mid, bb_low = bollinger(closes)
    stoch_k, stoch_d = stochastic(highs, lows, closes)
    vol_ratio = volume_ratio(volumes)
    hi = rolling_high(highs, 252)
    lo = rolling_low(lows, 252)

    out: List[BarIndicators] = []
    for i in range(len(bars)):
        e5, e20, e60 = ema5[i], ema20[i], ema60[i]
        ready = all(v is not None for v in (e5, e20, e60, rsi14[i], macd_line[i], bb_mid[i]))

        if e20 is not None and e60 is not None and e60 > 0:
            if e20 > e60 * 1.01:
                regime = "Bullish"
            elif e20 < e60 * 0.99:
                regime = "Bearish"
            else:
                regime = "Sideways"
        else:
            regime = "Sideways"

        tech: Dict[str, Any] = {}
        if ready:
            tech = {
                "ema5": e5,
                "ema20": e20,
                "ema60": e60,
                "rsi": rsi14[i] if rsi14[i] is not None else 50.0,
                "macd": macd_line[i] if macd_line[i] is not None else 0.0,
                "macd_signal": macd_sig[i] if macd_sig[i] is not None else 0.0,
                "macd_hist": macd_hist[i] if macd_hist[i] is not None else 0.0,
                "bb_upper": bb_up[i] if bb_up[i] is not None else closes[i],
                "bb_middle": bb_mid[i] if bb_mid[i] is not None else closes[i],
                "bb_lower": bb_low[i] if bb_low[i] is not None else closes[i],
                "stoch_k": stoch_k[i] if stoch_k[i] is not None else 50.0,
                "stoch_d": stoch_d[i] if stoch_d[i] is not None else 50.0,
                "volume_ratio": vol_ratio[i] if vol_ratio[i] is not None else 1.0,
            }

        out.append(
            BarIndicators(
                tech_data=tech,
                regime=regime,
                high_52w=hi[i] if hi[i] is not None else closes[i],
                low_52w=lo[i] if lo[i] is not None else closes[i],
                ready=ready,
            )
        )
    return out


# ═══════════════════════════════════════════════════════════════════
#  앙상블 평가기
# ═══════════════════════════════════════════════════════════════════

class EnsembleEvaluator:
    """프로덕션 3전략(Trend/Reversal/Breakout) 앙상블 신호 생성기.

    결합 규칙(투표 기반):
        buy  = Σ(weight × score) over 전략 with action == "BUY"
        sell = Σ(weight × score) over 전략 with action == "SELL"
        net  = (buy − sell) / Σ(weight)
        action = "BUY" if net >= net_threshold else "HOLD"

    설계 근거:
        단순 점수 평균은 "HOLD(점수 높음)"와 "SELL(점수 낮음)"를 구분하지 못한다
        (예: 강한 상승에서 Trend=HOLD 0.68, Reversal=SELL 0.00 → 평균이 희석됨).
        전략의 **행동(BUY/SELL)** 을 먼저 존중하고 점수·가중치로 크기를 정한다.
    """

    def __init__(self, net_threshold: float = 0.25) -> None:
        self.strategies = [
            TrendStrategy(weight=0.40),
            ReversalStrategy(weight=0.30),
            BreakoutStrategy(weight=0.30),
        ]
        self.net_threshold = net_threshold

    @property
    def warmup(self) -> int:
        return WARMUP_BARS

    def _net_score(self, results: Sequence[Any]) -> float:
        total_weight = sum(s.weight for s in self.strategies) or 1.0
        buy = sum(s.weight * r.score for s, r in zip(self.strategies, results) if r.action == "BUY")
        sell = sum(s.weight * r.score for s, r in zip(self.strategies, results) if r.action == "SELL")
        return (buy - sell) / total_weight

    async def net_scores(self, bars: Sequence[Dict[str, Any]]) -> List[Optional[float]]:
        """봉별 앙상블 net score(워밍업 구간은 None).

        전략 호출이 비싸므로 **1회만 계산**해 두고, 임계값 스윕은 이 값을
        비교하는 것만으로 수행한다(`scores_to_actions`).
        """
        indicators = compute_indicators(bars, warmup=self.warmup)

        scores: List[Optional[float]] = []
        for bar, ind in zip(bars, indicators):
            if not ind.ready:
                scores.append(None)
                continue

            price = float(bar.get("close", 0.0) or 0.0)
            payload = {
                "tech_data": ind.tech_data,
                "price": price,
                "regime": ind.regime,
                "high_52w": ind.high_52w,
                "low_52w": ind.low_52w,
            }
            results = [await s.analyze(payload) for s in self.strategies]
            scores.append(self._net_score(results))
        return scores

    async def signal_actions(self, bars: Sequence[Dict[str, Any]]) -> List[str]:
        """봉별 앙상블 액션("BUY"/"HOLD") 목록."""
        scores = await self.net_scores(bars)
        return scores_to_actions(scores, self.net_threshold)

    async def entry_indices(self, bars: Sequence[Dict[str, Any]]) -> List[int]:
        """신규 BUY 전환 지점(엣지 트리거)의 인덱스 목록."""
        actions = await self.signal_actions(bars)
        return list(_edge_indices(actions, 0))

    async def simulate(
        self, bars: Sequence[Dict[str, Any]], config: Optional[SimConfig] = None, ticker: str = "UNKNOWN"
    ) -> List[Any]:
        """앙상블 신호로 거래 목록을 생성한다(청산 규칙은 MA교차와 동일)."""
        actions = await self.signal_actions(bars)
        entries = list(_edge_indices(actions, 0))
        return simulate_entries(bars, entries, config, ticker)


def scores_to_actions(scores: Sequence[Optional[float]], threshold: float) -> List[str]:
    """net score 목록을 임계값으로 액션 목록으로 변환한다(워밍업은 HOLD)."""
    return ["BUY" if (s is not None and s >= threshold) else "HOLD" for s in scores]


def _edge_indices(actions: Sequence[str], offset: int) -> List[int]:
    """actions 구간에서 'BUY가 새로 시작되는' 로컬 인덱스를 offset 만큼 이동해 반환.

    폴드(구간)별 시뮬레이션에서는 구간 시작 시점의 상태를 새 출발로 본다
    (직전 구간에서 이어진 BUY 상태도 해당 구간 첫 봉에서 진입으로 취급).
    """
    entries: List[int] = []
    prev = "HOLD"
    for k, action in enumerate(actions):
        if action == "BUY" and prev != "BUY":
            entries.append(k + offset)
        prev = action
    return entries
