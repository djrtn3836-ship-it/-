"""모의 포트폴리오 청산 리허설 — 실제 상태를 복제하고 가상 미래 가격으로 청산 경로를 검증.

사용: python -X utf8 scripts/exit_rehearsal.py [--day YYYY-MM-DD] [--drift 0.0]
실제 상태 파일(data/disclosure_portfolio_state.json)은 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analytics import disclosure_portfolio as pf  # noqa: E402


class _FakeDB:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]):
        self._rows = rows

    async def get_ohlcv_range(self, ticker: str, start: str, end: str):
        return self._rows.get(ticker, [])

    async def close(self):
        return None


async def _history(ticker: str, end: str) -> list[dict[str, Any]]:
    from data.db_manager import DatabaseManager

    d = DatabaseManager()
    try:
        r = await d.get_ohlcv_range(ticker, "2026-08-01", end)
    finally:
        try:
            await d.close()
        except Exception:
            pass
    rows = [{"date": str(x["date"])[:10], "close": float(x["close"])} for x in (r or [])]
    if not rows:
        from scheduler.ohlcv_backfill import OHLCVBackfiller

        fetched, _ = await asyncio.to_thread(OHLCVBackfiller(delay=0.0).fetch_history, ticker, "1mo")
        rows = [{"date": x["date"], "close": float(x["close"])} for x in fetched if x.get("date")]
    return rows


def _business_days(start: str, end: str) -> list[str]:
    from datetime import date, timedelta

    y, m, dd = (int(x) for x in start.split("-"))
    ey, em, ed = (int(x) for x in end.split("-"))
    cur, last = date(y, m, dd), date(ey, em, ed)
    out = []
    while cur <= last:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-10-27")
    ap.add_argument("--drift", type=float, default=0.0, help="마지막 실측 종가 대비 가상 상승률")
    ap.add_argument("--from-day", default=None, help="가상 가격 시작 전일(기본: 오늘)")
    args = ap.parse_args()

    state = pf._load_state()
    positions = state.get("positions") or []
    if not positions:
        print("[SKIP] 보유 포지션 없음 — 청산 리허설 대상 없음")
        return 0

    rows: dict[str, list[dict[str, Any]]] = {}
    for p in positions:
        t = str(p["ticker"])
        last_real = args.from_day or datetime.now().strftime("%Y-%m-%d")
        hist = asyncio.run(_history(t, last_real))
        if not hist:
            print(f"[FAIL] {t} 이력 없음")
            return 1
        last_day = hist[-1]["date"]
        last_px = hist[-1]["close"]
        target = last_px * (1.0 + args.drift)
        have = {x["date"] for x in hist}
        for d in _business_days(last_day, args.day):
            if d not in have:
                hist.append({"date": d, "close": target})
        hist = [x for x in hist if x["date"] <= args.day]
        rows[t] = hist

    tmp = ROOT / "data" / "_rehearsal_state.json"
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    try:
        out = pf.run_portfolio(
            today=args.day,
            state_path=tmp,
            watch_log=ROOT / "data" / "_rehearsal_watch.jsonl",
            db=_FakeDB(rows),
            sizing="inverse_vol",
        )
        after = json.loads(tmp.read_text(encoding="utf-8"))
    finally:
        tmp.unlink(missing_ok=True)

    ok = True
    if out.get("closed") != len(positions):
        print(f"[FAIL] 청산 수 {out.get('closed')} != 보유 {len(positions)}")
        ok = False
    if out.get("positions") != 0:
        print(f"[FAIL] 잔여 포지션 {out.get('positions')}")
        ok = False
    freed = sum(
        float(p["shares"]) * rows[str(p["ticker"])][-1]["close"] * (1.0 - pf.COST)
        for p in after.get("closed", [])
    )
    exp_cash = float(state["cash"]) + freed
    if abs(float(after["cash"]) - exp_cash) > 1e-9:
        print(f"[FAIL] 현금 {after['cash']:.8f} != 기대 {exp_cash:.8f}")
        ok = False
    if after.get("positions"):
        print("[FAIL] 상태 파일에 포지션 잔존")
        ok = False
    for c in after.get("closed", []):
        if c.get("exit_date") != args.day:
            print(f"[FAIL] exit_date {c.get('exit_date')} != {args.day}")
            ok = False

    print("=" * 60)
    print(f"청산 리허설 ({args.day}, drift {args.drift:+.1%}) — 보유 {len(positions)}건")
    for c in after.get("closed", []):
        pnl = (float(c["exit_price"]) / float(c["entry_price"]) - 1.0) * 100.0
        print(f"  {c['ticker']} {c.get('name','')} {c['category']}: "
              f"{float(c['entry_price']):,.0f} → {float(c['exit_price']):,.0f} ({pnl:+.2f}%)")
    print(f"  총자산 {out.get('total_pct'):+.2f}% · 현금 {float(after['cash']):.4f} · 청산 {out.get('closed')} · 승률 {out.get('win_rate')}%")
    print("=" * 60)
    print("[PASS] 청산 경로 정상" if ok else "[FAIL] 청산 경로 이상")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
