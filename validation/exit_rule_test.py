# -*- coding: utf-8 -*-
"""validation/exit_rule_test.py — 매도(청산) 규칙 비교 검증 (S136, ②).

목적:
    진입은 같게 두고 **청산 방식만** 바꿔 기대값을 비교한다.
    개인투자자가 가장 크게 새는 구멍(손절 못함·익절 못함)을 실데이터로 측정.

진입 이벤트(우리 알림 조건 근사 — 실시간 감시 기준):
    거래량 ≥ 20일 평균×3  AND  |당일 변동| ≥ 2%   (같은 종목 5일 내 재이벤트는 제외)
    진입가 = 다음 날 시가 (신호는 장중 발생 → 익일 시가 진입이 현실적).

청산 규칙(최대 20거래일):
    고정 3/5/10/20일 · 트레일링(고점 종가 −5% 이탈) · 목표+7%/손절−3%(장중 터치,
    같은 날 동시 터치 시 손절 우선 보수) · MA20 이탈(종가 < MA20).
    모든 성과에서 왕복 비용 0.3% 차감. 아티팩트(±31% 일변동=분할류) 구간 제외.

추가: 이벤트 당일 폭 ≤15%(패닉권) 여부로 나눈 하위 표 (①의 구역 검증과 연결).

사용:
    python -X utf8 -m validation.exit_rule_test --db data/decisions.db
"""

from __future__ import annotations

import argparse
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analytics.market_breadth import S, breadth_at, load_universe

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "decisions.db"

VOL_MULT = 3.0
MIN_ABS_CHG = 0.02
RE_EVENT_GAP = 5      # 같은 종목 5일 내 재이벤트 제외(중복 창)
MAX_HOLD = 20
COST_PCT = 0.3        # 왕복 비용(%p)
ARTIFACT = 0.31       # 일변동 ±31% 초과 = 분할/감자 아티팩트
PANIC_BREADTH = 15.0


def find_events(series: Dict[str, S]) -> List[Tuple[S, int, str]]:
    """진입 이벤트 스캔 — (S, 진입일 index, 신호일 날짜).

    조건: 거래량 ≥ 20일 평균×3 AND |변동| ≥ 2% · 같은 종목 5일 내 재이벤트 제외 ·
    창 주변 ±31% 초과 일변동(분할류 아티팩트) 제외.
    """
    out: List[Tuple[S, int, str]] = []
    for _t, s in series.items():
        n = len(s.dates)
        if n < 60 or len(s.volume) != n:
            continue
        last = -100
        for i in range(21, n - 1):
            vbase = statistics.fmean(s.volume[i - 20: i])
            if vbase <= 0:
                continue
            chg = s.close[i] / s.close[i - 1] - 1.0
            if s.volume[i] >= vbase * VOL_MULT and abs(chg) >= MIN_ABS_CHG:
                if i - last < RE_EVENT_GAP:
                    continue
                if any(
                    abs(s.close[j] / s.close[j - 1] - 1.0) > ARTIFACT
                    for j in range(max(1, i - 1), min(n, i + 22))
                ):
                    continue
                out.append((s, i + 1, s.dates[i]))
                last = i
    return out


def simulate(
    close: Sequence[float], high: Sequence[float], low: Sequence[float], open_: Sequence[float],
    e: int, rule: str,
) -> Optional[Tuple[float, float]]:
    """진입일 index e(시가 진입) 기준 규칙 시뮬. 반환: (비용 차감 순수익률 %, MAE %)."""
    if e >= len(close) or open_[e] <= 0:
        return None
    entry = open_[e]
    end = min(e + MAX_HOLD, len(close))
    if end - e < 2:
        return None
    mae = min(low[e:end]) / entry * 100.0 - 100.0

    def _net(ex: float) -> float:
        return ex - COST_PCT

    if rule.startswith("고정"):
        k = int(rule.split()[1].replace("일", ""))
        j = e + k - 1
        if j >= len(close):
            return None
        return _net(close[j] / entry * 100.0 - 100.0), mae

    if rule == "트레일링-5%":
        peak = close[e]
        for j in range(e, end):
            peak = max(peak, close[j])
            if close[j] <= peak * 0.95:
                return _net(close[j] / entry * 100.0 - 100.0), mae
        return _net(close[end - 1] / entry * 100.0 - 100.0), mae

    if rule == "목표7/손절3":
        stop, tgt = entry * 0.97, entry * 1.07
        for j in range(e, end):
            if low[j] <= stop:
                return _net(-3.0), mae
            if high[j] >= tgt:
                return _net(+7.0), mae
        return _net(close[end - 1] / entry * 100.0 - 100.0), mae

    if rule == "MA20이탈":
        for j in range(e, end):
            lo = j - 20
            if lo < 0:
                continue
            ma = statistics.fmean(close[lo:j])
            if close[j] < ma:
                return _net(close[j] / entry * 100.0 - 100.0), mae
        return _net(close[end - 1] / entry * 100.0 - 100.0), mae

    return None


def _tstat(vals: Sequence[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    sd = statistics.stdev(vals)
    return statistics.fmean(vals) / (sd / math.sqrt(n)) if sd else 0.0


def stats_line(vals: Sequence[float]) -> Dict[str, Any]:
    wins = [v for v in vals if v > 0]
    losses = [abs(v) for v in vals if v < 0]
    return {
        "n": len(vals),
        "mean": statistics.fmean(vals) if vals else 0.0,
        "median": statistics.median(vals) if vals else 0.0,
        "win": len(wins) / len(vals) * 100.0 if vals else 0.0,
        "avg_w": statistics.fmean(wins) if wins else 0.0,
        "avg_l": statistics.fmean(losses) if losses else 0.0,
        "t": _tstat(vals),
    }


RULES = ["고정 3일", "고정 5일", "고정 10일", "고정 20일", "트레일링-5%", "목표7/손절3", "MA20이탈"]


def run_rules(
    events: Sequence[Tuple[S, int, str]],
    breadth_by_date: Dict[str, Optional[float]],
    panic_only: Optional[bool] = None,
) -> Dict[str, Dict[str, Any]]:
    """이벤트들에 대해 규칙별 통계. panic_only=True면 이벤트일 폭≤15만."""
    out: Dict[str, Dict[str, Any]] = {}
    for rule in RULES:
        vals: List[float] = []
        maes: List[float] = []
        yearly: Dict[str, List[float]] = defaultdict(list)
        for s, e, d in events:
            if panic_only is not None:
                b = breadth_by_date.get(d)
                if b is None:
                    continue
                if panic_only and b > PANIC_BREADTH:
                    continue
                if (not panic_only) and b <= PANIC_BREADTH:
                    continue
            r = simulate(s.close, s.high, s.low, s.open, e, rule)
            if r is None:
                continue
            vals.append(r[0])
            maes.append(r[1])
            yearly[d[:4]].append(r[0])
        st = stats_line(vals)
        st["mae"] = statistics.fmean(maes) if maes else 0.0
        st["years"] = {y: statistics.fmean(v) for y, v in sorted(yearly.items())}
        out[rule] = st
    return out


def _print_table(title: str, res: Dict[str, Dict[str, Any]]) -> None:
    print(f"\n■ {title}")
    print("  규칙            n    평균%   중앙%  승률%  평균익 평균손   t    MAE%  연도별평균")
    for rule in RULES:
        st = res.get(rule) or {}
        if not st.get("n"):
            continue
        yrs = " ".join(f"{y[2:]}:{v:+.1f}" for y, v in st.get("years", {}).items())
        print(
            f"  {rule:<13} {st['n']:>5} {st['mean']:>+7.2f} {st['median']:>+7.2f} "
            f"{st['win']:>5.0f} {st['avg_w']:>+6.2f} {st['avg_l']:>6.2f} {st['t']:>+5.2f} "
            f"{st['mae']:>+6.2f}  {yrs}"
        )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="매도(청산) 규칙 비교 검증")
    ap.add_argument("--db", type=str, default=str(DB_PATH))
    ap.add_argument("--limit-tickers", type=int, default=0, help="디버그용 종목 수 제한")
    args = ap.parse_args(argv)

    db = Path(args.db)
    series = load_universe(db, min_days=60, with_open_volume=True)
    universe = load_universe(db, min_days=30)
    if args.limit_tickers:
        series = dict(list(series.items())[: args.limit_tickers])

    events = find_events(series)
    if not events:
        print("이벤트 없음")
        return 1

    print(f"■ 매도규칙 비교 — 진입 이벤트 {len(events)}건 (거래량×3 + |변동|≥2%, 익일 시가 진입, 비용 {COST_PCT}%p 차감)")

    dates = sorted({d for _, _, d in events})
    breadth_by_date: Dict[str, Optional[float]] = {d: breadth_at(universe, d) for d in dates}

    all_res = run_rules(events, breadth_by_date)
    _print_table("전체 이벤트", all_res)
    _print_table("패닉권 이벤트(당일 폭 ≤15%)", run_rules(events, breadth_by_date, panic_only=True))
    _print_table("비패닉 이벤트", run_rules(events, breadth_by_date, panic_only=False))

    best = max(
        (r for r in RULES if all_res.get(r, {}).get("n")),
        key=lambda r: all_res[r]["mean"],
        default=None,
    )
    print(f"\n기대값 최고 규칙: {best} ({all_res[best]['mean']:+.2f}%)" if best else "")
    print("※ 진입 자체가 무엣지(알림 근사)일 가능성 — 규칙 간 '상대 비교'로 읽을 것. 단일 지표 단정 금지.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
