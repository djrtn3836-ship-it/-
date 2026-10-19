# -*- coding: utf-8 -*-
"""scheduler/signal_logger.py — 신호 기록부 (S138).

배경 (사용자 의도): 신호를 여러 갈래로 만들어 **쌓이게** 해야 매매에 도움이 된다.
    S137 조건 스캔에서 A(생존)·B(경계)·C(참고) 등급과 발동 빈도를 확보했다.
    이제 매일 활성 신호를 기록하고, 일정 기간 뒤 **자동 채점**해 신호별
    실시간 성적표를 축적한다(검증→기록→재검증 루프의 마지막 고리).

동작 (평일 17:20 스케줄):
    1) 지수·매크로 최근 시세 갱신(^KS11·^KQ11·069500.KS + VIX·나스닥·달러·금리·구리)
       — 인덱스가 갱신되지 않아 채점이 영원히 안 되는 문제 방지
    2) 오늘 활성 신호를 data/signal_log.jsonl 에 1회 기록(날짜·신호 중복 방지)
    3) 5거래일 경과 신호를 KOSPI 전방수익으로 채점 → data/signal_log_settled.jsonl
    4) scorecard() — 신호별 n·평균 전방수익·적중률 (주간 성적표에 포함)

신호 레지스트리는 condition_sweep_test.py의 A/B/C 조건과 1:1 대응(단일 출처).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("signal_logger")

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "decisions.db"
LOG_PATH = ROOT / "data" / "signal_log.jsonl"
SETTLED_PATH = ROOT / "data" / "signal_log_settled.jsonl"
HORIZON = 5

REFRESH_TICKERS = ["^KS11", "^KQ11", "069500.KS", "^VIX", "^IXIC", "DX-Y.NYB", "^TNX", "HG=F"]

# (키, 등급, 표시명, 판정 함수) — ctx: breadth / kospi5d / nasdaq5d / vol_ratio
SIGNALS: List[tuple] = [
    ("breadth_le10", "B", "폭≤10%", lambda c: c.get("breadth") is not None and c["breadth"] <= 10.0),
    ("breadth_le15", "A", "폭≤15%(패닉권)", lambda c: c.get("breadth") is not None and c["breadth"] <= 15.0),
    (
        "breadth_le15_down", "A", "폭≤15% + 하락중",
        lambda c: (
            c.get("breadth") is not None and c["breadth"] <= 15.0
            and c.get("kospi5d") is not None and c["kospi5d"] < 0
        ),
    ),
    (
        "kospi_nasdaq_gap_le-5p", "A", "KOSPI-나스닥 괴리≤-5%p",
        lambda c: c.get("gap5") is not None and c["gap5"] <= -5.0,
    ),
    ("breadth_le20", "C", "폭≤20%", lambda c: c.get("breadth") is not None and c["breadth"] <= 20.0),
    ("breadth_ge80", "C", "폭≥80%", lambda c: c.get("breadth") is not None and c["breadth"] >= 80.0),
    ("kospi5_le-5", "C", "KOSPI 5일≤-5%", lambda c: c.get("kospi5d") is not None and c["kospi5d"] <= -5.0),
    ("kospi5_ge+5", "C", "KOSPI 5일≥+5%", lambda c: c.get("kospi5d") is not None and c["kospi5d"] >= 5.0),
    ("volume_surge", "C", "시장 거래량≥MA20×1.5", lambda c: c.get("vol_ratio") is not None and c["vol_ratio"] >= 1.5),
    ("nasdaq5_ge+5", "C", "나스닥 5일≥+5%", lambda c: c.get("nasdaq5d") is not None and c["nasdaq5d"] >= 5.0),
]


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    except FileNotFoundError:
        return []
    return rows


def _append_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _pct5(series: Any, asof: str) -> Optional[float]:
    """시리즈의 asof 시점 기준 5거래일 퍼센트 변화(없으면 None)."""
    if not series or not series.dates:
        return None
    i = len(series.dates) - 1
    if series.dates[i] != asof:
        return None
    if i < 5:
        return None
    base = series.close[i - 5]
    return (series.close[i] / base - 1.0) * 100.0 if base else None


def compute_context(db_path: Path = DB_PATH) -> Dict[str, Any]:
    """오늘 기준 신호 재료(폭·KOSPI5일·나스닥5일·시장거래량비)."""
    from analytics.market_breadth import compute_breadth, load_breadth_series, load_index

    ctx: Dict[str, Any] = {}
    try:
        b = compute_breadth(load_breadth_series(db_path))
        ctx["breadth"] = b.get("above_ma20_pct")
        ctx["ref_date"] = b.get("ref_date")
    except Exception as e:
        logger.warning(f"폭 계산 실패: {e}")

    kidx = load_index(db_path, "^KS11")
    nidx = load_index(db_path, "^IXIC")
    ctx["kospi5d"] = _pct5(kidx, kidx.dates[-1]) if kidx.dates else None
    if nidx.dates:
        ctx["nasdaq5d"] = (nidx.close[-1] / nidx.close[-6] - 1.0) * 100.0 if len(nidx.close) >= 6 else None
    if ctx.get("kospi5d") is not None and ctx.get("nasdaq5d") is not None:
        ctx["gap5"] = ctx["kospi5d"] - ctx["nasdaq5d"]

    try:
        import sqlite3

        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT date, SUM(volume), COUNT(*) FROM ohlcv "
                "WHERE ticker NOT LIKE '^%' AND volume > 0 GROUP BY date ORDER BY date DESC LIMIT 21"
            ).fetchall()
        finally:
            con.close()
        if len(rows) >= 21:
            means = [float(s) / float(c) for _, s, c in rows[1:21] if c]
            base = sum(means) / len(means) if means else 0.0
            if base > 0:
                ctx["vol_ratio"] = (float(rows[0][1]) / float(rows[0][2])) / base
    except Exception as e:
        logger.warning(f"거래량비 계산 실패: {e}")
    return ctx


def latest_signals(*, log_path: Path = LOG_PATH, day: Optional[str] = None) -> Dict[str, Any]:
    """가장 최근(또는 지정일) 기록된 신호 목록. 기록 없으면 {}."""
    rows = _load_jsonl(log_path)
    if not rows:
        return {}
    target = day or max(str(r.get("date") or "") for r in rows)
    sel = [r for r in rows if str(r.get("date")) == target]
    sel.sort(key=lambda r: (str(r.get("grade") or ""), str(r.get("signal") or "")))
    return {"date": target, "signals": sel}


def active_signals(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for key, grade, label, fn in SIGNALS:
        try:
            if fn(ctx):
                out.append({"signal": key, "grade": grade, "label": label})
        except Exception:
            continue
    return out


def log_today(
    *,
    db_path: Path = DB_PATH,
    log_path: Path = LOG_PATH,
    ctx: Optional[Dict[str, Any]] = None,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    """오늘 활성 신호 기록(날짜·신호 중복 방지). 반환: 요약."""
    day = today or date.today().isoformat()
    c = ctx if ctx is not None else compute_context(db_path)
    actives = active_signals(c)
    existing = {(r.get("date"), r.get("signal")) for r in _load_jsonl(log_path)}
    fresh = []
    for a in actives:
        if (day, a["signal"]) in existing:
            continue
        fresh.append({
            "date": day,
            "signal": a["signal"],
            "grade": a["grade"],
            "label": a["label"],
            "value": None,
            "breadth": c.get("breadth"),
            "kospi5d": c.get("kospi5d"),
            "gap5": c.get("gap5"),
            "vol_ratio": c.get("vol_ratio"),
        })
    _append_jsonl(log_path, fresh)
    if fresh:
        logger.info(f"신호 기록: {day} → {[r['signal'] for r in fresh]}")
    else:
        logger.info(f"신호 기록: {day} → 신규 없음(활성 {len(actives)}·중복 제외)")
    return {"date": day, "active": [a["signal"] for a in actives], "logged": len(fresh), "ctx": c}


def settle_all(
    *,
    db_path: Path = DB_PATH,
    log_path: Path = LOG_PATH,
    settled_path: Path = SETTLED_PATH,
    horizon: int = HORIZON,
    index: Optional[Any] = None,
) -> Dict[str, Any]:
    """horizon거래일 경과 신호를 KOSPI 전방수익으로 채점(중복 방지). 반환: 요약."""
    if index is None:
        from analytics.market_breadth import load_index

        index = load_index(db_path, "^KS11")
    dates = list(index.dates)
    pos = {d: i for i, d in enumerate(dates)}
    settled_keys = {(r.get("date"), r.get("signal")) for r in _load_jsonl(settled_path)}
    fresh: List[Dict[str, Any]] = []
    pending = 0
    for r in _load_jsonl(log_path):
        key = (r.get("date"), r.get("signal"))
        if key in settled_keys:
            continue
        i = pos.get(str(r.get("date")))
        if i is None or i + horizon >= len(dates):
            pending += 1
            continue
        entry, exit_ = index.close[i], index.close[i + horizon]
        if not entry or not exit_:
            pending += 1
            continue
        fwd = (exit_ / entry - 1.0) * 100.0
        fresh.append({**r, "horizon": horizon, "fwd_pct": round(fwd, 3), "hit": fwd > 0})
    _append_jsonl(settled_path, fresh)
    if fresh:
        logger.info(f"신호 채점: {len(fresh)}건 (horizon {horizon}일) · 대기 {pending}건")
    return {"settled": len(fresh), "pending": pending}


def scorecard(*, settled_path: Path = SETTLED_PATH) -> List[Dict[str, Any]]:
    """신호별 성적표(n·평균·적중률)."""
    rows = _load_jsonl(settled_path)
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = str(r.get("signal"))
        a = agg.setdefault(key, {"signal": key, "grade": r.get("grade"), "label": r.get("label"),
                                 "n": 0, "sum": 0.0, "hits": 0})
        a["n"] += 1
        a["sum"] += float(r.get("fwd_pct") or 0.0)
        a["hits"] += 1 if r.get("hit") else 0
    out = []
    for a in agg.values():
        a["avg"] = a.pop("sum") / a["n"] if a["n"] else 0.0
        a["hit"] = a["hits"] / a["n"] * 100.0 if a["n"] else 0.0
        out.append(a)
    out.sort(key=lambda x: (-x["n"], str(x["signal"])))
    return out


def scorecard_text(settled_path: Path = SETTLED_PATH) -> str:
    """주간 성적표용 텍스트."""
    rows = scorecard(settled_path=settled_path)
    if not rows:
        return "■ 신호 기록부: 채점된 신호 없음(5거래일 경과 대기)"
    lines = ["■ 신호 기록부 성적(5거래일, KOSPI 전방수익)"]
    for r in rows:
        lines.append(
            f"  [{r['grade']}] {r['label']} — n={r['n']} 평균 {r['avg']:+.2f}% 적중 {r['hit']:.0f}%"
        )
    return "\n".join(lines)


async def refresh_series(
    fetcher: Optional[Callable[[str, str], List[Dict[str, Any]]]] = None,
    db: Optional[Any] = None,
) -> Dict[str, int]:
    """지수·매크로 최근 1개월 갱신(채점 가능성 보장). 앱 내부 실행(같은 DB 커넥션)."""
    from scheduler.index_backfill import _fetch_index

    fetch = fetcher or _fetch_index
    owns = db is None
    if owns:
        from data.db_manager import DatabaseManager

        db = DatabaseManager()
    out: Dict[str, int] = {}
    try:
        for tk in REFRESH_TICKERS:
            try:
                rows = fetch(tk, "1mo")
            except Exception as e:
                logger.warning(f"{tk} 갱신 실패: {e}")
                out[tk] = 0
                continue
            if not rows:
                out[tk] = 0
                continue
            recs = [
                (tk, r["date"], r.get("open", 0.0), r.get("high", 0.0),
                 r.get("low", 0.0), r.get("close", 0.0), r.get("volume", 0))
                for r in rows
            ]
            try:
                out[tk] = int(await db.save_ohlcv_batch(recs) or 0)
            except Exception as e:
                logger.warning(f"{tk} 저장 실패: {e}")
                out[tk] = 0
    finally:
        if owns:
            try:
                await db.flush_pending()
            finally:
                await db.close()
    return out


async def run_daily() -> Dict[str, Any]:
    """스케줄러 진입점: 시세 갱신 → 채점 → 기록. 반환: 요약."""
    refreshed = await refresh_series()
    settled = settle_all()
    logged = log_today()
    result = {"refreshed": sum(1 for v in refreshed.values() if v), "settled": settled["settled"],
              "logged": logged["logged"], "active": logged["active"]}
    logger.info(f"신호 기록부 완료: {result}")
    return result


async def scheduled_signal_log() -> Dict[str, Any]:
    """스케줄러용 래퍼(예외 흡수 — 다른 잡 보호)."""
    try:
        return await run_daily()
    except Exception as e:
        logger.error(f"신호 기록부 실패: {e}")
        return {"error": str(e)[:200]}
