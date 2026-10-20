# -*- coding: utf-8 -*-
"""공시 신호 전략 백테스트 (S145).

이벤트 통계가 아니라 '실제로 따라 하면 수익이 나는가'를 검정한다.
- 진입: 신호일 종가(14:50 알림 가정) / 비교: 다음날 시가
- 보유: 유형별(자사주·무상증자 5일, 소각·합병 1일)
- 비용: 왕복 0.3%(매수 0.15% + 매도 0.15%)
- 포트폴리오: 동시 최대 5종목, 균등 배분, 현금 보유
- 비교: 무작위 진입(동일 거래수) · KOSPI 보유
"""
from __future__ import annotations

import argparse
import bisect
import json
import random
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "disclosures_history.json"
BODY_CACHE = Path(__file__).resolve().parent.parent / "data" / "buyback_ratios.json"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "disclosure_strategy.json"
KOSPI = "^KS11"
START = "2021-10-19"
COST = 0.0015
MAX_POS = 5
DEDUPE_GAP = 20

HOLD_DAYS = {"자사주매입": 5, "무상증자": 5, "주식소각": 1, "합병": 1}


def load_series(rows: List[Dict[str, Any]]) -> Tuple[List[str], List[float], List[float], List[float]]:
    dates, opens, closes, vols = [], [], [], []
    for r in rows:
        try:
            c = float(r.get("close") or 0.0)
        except (TypeError, ValueError):
            continue
        if c <= 0:
            continue
        dates.append(str(r.get("date"))[:10])
        closes.append(c)
        try:
            opens.append(float(r.get("open") or c))
        except (TypeError, ValueError):
            opens.append(c)
        try:
            vols.append(float(r.get("volume") or 0.0))
        except (TypeError, ValueError):
            vols.append(0.0)
    return dates, opens, closes, vols


def build_signals(
    tickers: Dict[str, Any],
    series: Dict[str, Tuple[List[str], List[float], List[float], List[float]]],
    *,
    categories: Optional[set] = None,
    body_cache: Optional[Dict[str, Any]] = None,
    big_only: bool = False,
    entry: str = "close",
) -> List[Dict[str, Any]]:
    """공시 이벤트 → 진입/청산 계획. 종목별 20거래일 중복 제거."""
    from validation.disclosure_event_study import classify_title, parse_date

    cats = categories or set(HOLD_DAYS)
    out: List[Dict[str, Any]] = []
    for ticker, info in tickers.items():
        if ticker not in series:
            continue
        dates, opens, closes, vols = series[ticker]
        if len(dates) < 30:
            continue
        last_idx: Dict[str, int] = {}
        for ev in sorted(info.get("events", []), key=lambda e: str(e.get("date"))):
            cat = classify_title(ev.get("title", ""))
            if cat not in cats:
                continue
            dt = parse_date(ev.get("date", ""))
            if not dt:
                continue
            base = bisect.bisect_left(dates, dt.strftime("%Y-%m-%d"))
            if base + 2 >= len(dates):
                continue
            prev = last_idx.get(cat)
            if prev is not None and base - prev < DEDUPE_GAP:
                continue
            last_idx[cat] = base
            if entry == "open":
                e_idx = base + 1
                entry_px = opens[e_idx]
            else:
                e_idx = base
                entry_px = closes[e_idx]
            exit_idx = min(e_idx + HOLD_DAYS[cat], len(dates) - 1)
            if exit_idx <= e_idx or entry_px <= 0:
                continue
            if big_only:
                body = (body_cache or {}).get(str(ev.get("rcept_no") or "")) or {}
                amt = body.get("buy_amount")
                if not amt:
                    continue
                avg_turn = statistics.mean(
                    [closes[i] * vols[i] for i in range(max(0, base - 19), base + 1) if vols[i] > 0]
                ) if any(vols[i] > 0 for i in range(max(0, base - 19), base + 1)) else 0.0
                if avg_turn <= 0 or amt / avg_turn < 1.0:
                    continue
            out.append(
                {
                    "ticker": ticker,
                    "category": cat,
                    "entry_date": dates[e_idx],
                    "entry_price": entry_px,
                    "exit_date": dates[exit_idx],
                    "exit_idx": exit_idx,
                    "hold": HOLD_DAYS[cat],
                }
            )
    out.sort(key=lambda s: (s["entry_date"], s["ticker"]))
    return out


def _close_on(ticker: str, day: str, series, fallback_price: float) -> float:
    dates, _, closes, _ = series[ticker]
    i = bisect.bisect_right(dates, day) - 1
    if i < 0:
        return fallback_price
    return closes[i]


def simulate(
    signals: List[Dict[str, Any]],
    series: Dict[str, Tuple[List[str], List[float], List[float], List[float]]],
    k_dates: List[str],
    *,
    max_positions: int = MAX_POS,
    cost: float = COST,
    start_cash: float = 1.0,
    sizing: str = "equal",
) -> Dict[str, Any]:
    """일별 포트폴리오 시뮬레이션(종가 진입/청산, 비용 반영)."""
    by_entry: Dict[str, List[Dict[str, Any]]] = {}
    for s in signals:
        by_entry.setdefault(s["entry_date"], []).append(s)

    cash = start_cash
    positions: List[Dict[str, Any]] = []
    equity_curve: List[float] = []
    holding_days = 0

    for day in k_dates:
        if day < START:
            equity_curve.append(cash)
            continue
        # ① 청산
        still: List[Dict[str, Any]] = []
        for p in positions:
            if day >= p["exit_date"]:
                px = _close_on(p["ticker"], day, series, p["entry_price"])
                cash += p["shares"] * px * (1.0 - cost)
            else:
                still.append(p)
        # ② 신규 진입
        for s in by_entry.get(day, []):
            if len(still) >= max_positions:
                break
            if any(p["ticker"] == s["ticker"] for p in still):
                continue
            px = s["entry_price"]
            if px <= 0:
                continue
            equity = cash + sum(
                p["shares"] * _close_on(p["ticker"], day, series, p["entry_price"]) for p in still
            )
            notional = equity / max_positions
            if sizing == "inverse_vol":
                dates_t, _, closes_t, _ = series.get(s["ticker"], ([], [], [], []))
                i = bisect.bisect_right(dates_t, day) - 1
                if i >= 20:
                    rets = [closes_t[k] / closes_t[k - 1] - 1.0 for k in range(i - 19, i + 1)]
                    sd = statistics.pstdev(rets)
                    if sd > 0:
                        ann = sd * (252 ** 0.5)
                        notional *= max(0.5, min(1.5, 0.15 / ann))
            if notional > cash:
                notional = cash
            if notional <= 0:
                continue
            shares = notional / (px * (1.0 + cost))
            cash -= notional
            still.append(
                {
                    "ticker": s["ticker"],
                    "category": s["category"],
                    "entry_price": px,
                    "shares": shares,
                    "exit_date": s["exit_date"],
                }
            )
        positions = still
        if positions:
            holding_days += 1
        equity = cash + sum(
            p["shares"] * _close_on(p["ticker"], day, series, p["entry_price"]) for p in positions
        )
        equity_curve.append(equity)

    return {"equity": equity_curve, "holding_days": holding_days}


def metrics(equity: List[float], days: List[str]) -> Dict[str, Any]:
    curve = [e for e in equity if e > 0] or [1.0]
    rets = [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve))]
    n = len(days)
    years = max(n / 252.0, 1e-9)
    cagr = (curve[-1] / curve[0]) ** (1.0 / years) - 1.0 if curve[0] > 0 else 0.0
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    sharpe = (statistics.mean(rets) / sd * (252 ** 0.5)) if sd > 0 else 0.0
    peak = curve[0]
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, 1.0 - v / peak if peak > 0 else 0.0)
    return {
        "days": n,
        "cagr": round(cagr * 100.0, 2),
        "sharpe": round(sharpe, 2),
        "mdd": round(mdd * 100.0, 1),
        "total": round((curve[-1] / curve[0] - 1.0) * 100.0, 1),
    }


def yearly_returns(equity: List[float], dates: List[str]) -> Dict[str, float]:
    """연도별 수익률(%)."""
    out: Dict[str, float] = {}
    start_val = equity[0]
    for i in range(1, len(dates)):
        if dates[i][:4] != dates[i - 1][:4]:
            out[dates[i - 1][:4]] = round((equity[i - 1] / start_val - 1.0) * 100.0, 1)
            start_val = equity[i - 1]
    if dates:
        out[dates[-1][:4]] = round((equity[-1] / start_val - 1.0) * 100.0, 1)
    return out


def trade_stats(signals: List[Dict[str, Any]], series, cost: float = COST) -> Dict[str, Any]:
    rets = []
    for s in signals:
        px_in = s["entry_price"]
        px_out = _close_on(s["ticker"], s["exit_date"], series, px_in)
        if px_in > 0:
            rets.append(px_out / px_in - 1.0 - 2 * cost)
    if not rets:
        return {"n": 0}
    return {
        "n": len(rets),
        "avg_net": round(statistics.mean(rets) * 100.0, 2),
        "median_net": round(statistics.median(rets) * 100.0, 2),
        "win": round(sum(1 for r in rets if r > 0) / len(rets) * 100.0, 1),
    }


def run_strategy(
    *,
    history_path: Path = HISTORY_PATH,
    body_cache_path: Path = BODY_CACHE,
    logger: Any = None,
) -> Dict[str, Any]:
    import asyncio

    from data.db_manager import DatabaseManager
    from validation.disclosure_event_study import classify_title

    hist = json.loads(history_path.read_text(encoding="utf-8"))
    tickers = hist.get("tickers", {})
    end = datetime.now().strftime("%Y-%m-%d")
    body_cache: Dict[str, Any] = {}
    if body_cache_path.exists():
        try:
            body_cache = json.loads(body_cache_path.read_text(encoding="utf-8"))
        except Exception:
            body_cache = {}

    needed = set()
    for ticker, info in tickers.items():
        for ev in info.get("events", []):
            if classify_title(ev.get("title", "")) in HOLD_DAYS:
                needed.add(ticker)
                break

    async def _load():
        db = DatabaseManager()
        try:
            k_rows = await db.get_ohlcv_range(KOSPI, START, end)
            k_dates, _, k_closes, _ = load_series(k_rows)
            series: Dict[str, Any] = {}
            for t in sorted(needed):
                r = await db.get_ohlcv_range(t, START, end)
                if r:
                    series[t] = load_series(r)
            return k_dates, k_closes, series
        finally:
            try:
                await db.close()
            except Exception:
                pass

    k_dates, k_closes, series = asyncio.run(_load())
    if logger:
        logger.info(f"전략 백테스트: 종목 {len(series)} · 거래일 {len(k_dates)}")

    variants: Dict[str, Any] = {}
    specs = {
        "A_all": {"categories": set(HOLD_DAYS), "entry": "close"},
        "A_buyback": {"categories": {"자사주매입"}, "entry": "close"},
        "A_buyback_big": {"categories": {"자사주매입"}, "entry": "close", "big_only": True},
        "A_next_open": {"categories": set(HOLD_DAYS), "entry": "open"},
    }
    for name, spec in specs.items():
        sigs = build_signals(tickers, series, body_cache=body_cache, **spec)
        sim = simulate(sigs, series, k_dates)
        variants[name] = {
            "signals": len(sigs),
            "trades": trade_stats(sigs, series),
            "portfolio": metrics(sim["equity"], k_dates),
            "exposure": round(sim["holding_days"] / max(len(k_dates), 1) * 100.0, 1),
        }

    # 민감도: 비용 0.6% / 동시보유 3·10
    sigs_all = build_signals(tickers, series, categories=set(HOLD_DAYS), entry="close")
    for name, kw in (
        ("A_cost06", {"cost": 0.003}),
        ("A_max3", {"max_positions": 3}),
        ("A_max10", {"max_positions": 10}),
    ):
        sim_s = simulate(sigs_all, series, k_dates, **kw)
        variants[name] = {
            "signals": len(sigs_all),
            "trades": trade_stats(sigs_all, series, cost=kw.get("cost", COST)),
            "portfolio": metrics(sim_s["equity"], k_dates),
            "exposure": round(sim_s["holding_days"] / max(len(k_dates), 1) * 100.0, 1),
        }
    variants["A_all"]["yearly"] = yearly_returns(
        simulate(sigs_all, series, k_dates)["equity"], k_dates
    )

    # 스트레스: 비용 1.0% / 상위 5% 수익 제외 / 구간 분할 / 역변동성
    for name, kw in (
        ("A_cost10", {"cost": 0.005}),
        ("A_invvol", {"sizing": "inverse_vol"}),
    ):
        sim_s = simulate(sigs_all, series, k_dates, **kw)
        variants[name] = {
            "signals": len(sigs_all),
            "trades": trade_stats(sigs_all, series, cost=kw.get("cost", COST)),
            "portfolio": metrics(sim_s["equity"], k_dates),
            "exposure": round(sim_s["holding_days"] / max(len(k_dates), 1) * 100.0, 1),
        }

    def _net(x):
        px_out = _close_on(x["ticker"], x["exit_date"], series, x["entry_price"])
        return px_out / x["entry_price"] - 1.0 - 2 * COST

    kept = sorted(sigs_all, key=_net, reverse=True)[max(1, len(sigs_all) // 20):]
    sim_d = simulate(kept, series, k_dates)
    variants["A_drop_top5"] = {
        "signals": len(kept),
        "trades": trade_stats(kept, series),
        "portfolio": metrics(sim_d["equity"], k_dates),
        "exposure": round(sim_d["holding_days"] / max(len(k_dates), 1) * 100.0, 1),
    }

    for name, lo, hi in (("A_2022_23", "2022-01-01", "2023-12-31"),
                         ("A_2024_26", "2024-01-01", "2026-12-31")):
        part = [x for x in sigs_all if lo <= x["entry_date"] <= hi]
        kd = [d for d in k_dates if lo <= d <= hi]
        if not kd:
            continue
        sim_p = simulate(part, series, kd)
        variants[name] = {
            "signals": len(part),
            "trades": trade_stats(part, series),
            "portfolio": metrics(sim_p["equity"], kd),
            "exposure": round(sim_p["holding_days"] / max(len(kd), 1) * 100.0, 1),
        }

    # 무작위 벤치(동일 거래수·동일 보유기간)
    rng = random.Random(42)
    base_sigs = build_signals(tickers, series, categories=set(HOLD_DAYS), entry="close")
    rand_sigs: List[Dict[str, Any]] = []
    ticker_pool = sorted(series)
    for s in base_sigs:
        t = rng.choice(ticker_pool)
        dates, opens, closes, _ = series[t]
        if len(dates) < 30:
            continue
        i = rng.randrange(0, len(dates) - max(HOLD_DAYS.values()) - 1)
        j = min(i + HOLD_DAYS[s["category"]], len(dates) - 1)
        rand_sigs.append(
            {"ticker": t, "category": s["category"], "entry_date": dates[i],
             "entry_price": closes[i], "exit_date": dates[j], "exit_idx": j, "hold": HOLD_DAYS[s["category"]]}
        )
    rand_sigs.sort(key=lambda x: (x["entry_date"], x["ticker"]))
    sim_r = simulate(rand_sigs, series, k_dates)
    variants["random"] = {
        "signals": len(rand_sigs),
        "trades": trade_stats(rand_sigs, series),
        "portfolio": metrics(sim_r["equity"], k_dates),
        "exposure": round(sim_r["holding_days"] / max(len(k_dates), 1) * 100.0, 1),
    }

    bench = metrics([c / k_closes[0] for c in k_closes], k_dates)
    variants["kospi"] = {"signals": 0, "trades": {"n": 0}, "portfolio": bench, "exposure": 100.0}
    return {"variants": variants, "period": [k_dates[0], k_dates[-1]], "cost_roundtrip": COST * 2}


def format_result(res: Dict[str, Any]) -> str:
    lines = [f"공시 신호 전략 백테스트 ({res['period'][0]} ~ {res['period'][1]}, 왕복비용 {res['cost_roundtrip']*100:.1f}%)", ""]
    lines.append(f"{'전략':<16}{'거래':>6}{'CAGR':>9}{'Sharpe':>8}{'MDD':>7}{'승률':>7}{'평균(순)':>10}{'노출':>7}")
    labels = {
        "A_all": "A_전체", "A_buyback": "A_자사주", "A_buyback_big": "A_자사주_대형",
        "A_next_open": "A_다음날시가", "A_cost06": "A_비용0.6%",
        "A_cost10": "A_비용1.0%", "A_drop_top5": "A_상위5%제외",
        "A_invvol": "A_역변동성", "A_2022_23": "A_22-23", "A_2024_26": "A_24-26",
        "A_max3": "A_동시3", "A_max10": "A_동시10",
        "random": "무작위", "kospi": "KOSPI",
    }
    for name, v in res["variants"].items():
        p = v["portfolio"]
        t = v["trades"]
        lines.append(
            f"{labels.get(name, name):<16}{v['signals']:>6}{p['cagr']:>8.1f}%{p['sharpe']:>8.2f}"
            f"{p['mdd']:>6.1f}%{t.get('win', 0):>6.1f}%{t.get('avg_net', 0):>9.2f}%{v['exposure']:>6.1f}%"
        )
    yearly = (res["variants"].get("A_all") or {}).get("yearly") or {}
    if yearly:
        lines.append("")
        lines.append("A_전체 연도별: " + " · ".join(f"{y} {v:+.1f}%" for y, v in sorted(yearly.items())))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-json", action="store_true")
    args = ap.parse_args()
    from core.logger import setup_logger

    logger = setup_logger("disclosure_strategy")
    res = run_strategy(logger=logger)
    print(format_result(res))
    if args.save_json:
        OUT_PATH.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
