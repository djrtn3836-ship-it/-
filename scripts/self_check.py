# -*- coding: utf-8 -*-
"""자체 점검 (S149) — 데이터·상태·설정 정합성.

읽기 전용. 주말 유지보수 또는 수동 실행:
    python -X utf8 scripts/self_check.py
"""
from __future__ import annotations

import ast
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FAILS: list[str] = []
WARNS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)


def warn(msg: str) -> None:
    WARNS.append(msg)


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    bad = 0
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    if bad:
        fail(f"{path.name}: JSON 파싱 오류 {bad}행")
    return rows


def check_jsonl() -> None:
    specs = {
        "signal_log.jsonl": ("date", "signal"),
        "signal_log_settled.jsonl": ("date", "signal"),
        "disclosure_watch_log.jsonl": ("date", "ticker", "title"),
        "disclosure_watch_settled.jsonl": ("date", "ticker", "category"),
        "sentiment_history.jsonl": ("date", "ticker"),
    }
    for name, keys in specs.items():
        rows = load_jsonl(DATA / name)
        if not rows:
            continue
        c = Counter(tuple(str(r.get(k)) for k in keys) for r in rows)
        dups = {k: v for k, v in c.items() if v > 1}
        if dups:
            fail(f"{name}: 중복 키 {len(dups)}건 (예: {list(dups)[:2]})")


def check_portfolio() -> None:
    p = DATA / "disclosure_portfolio_state.json"
    if not p.exists():
        return
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        fail(f"포트폴리오 상태 파싱 실패: {e}")
        return
    cash = float(st.get("cash", 0))
    positions = st.get("positions") or []
    if cash < -1e-9:
        fail(f"포트폴리오 현금 음수: {cash}")
    for pos in positions:
        for key in ("ticker", "entry_price", "shares", "entry_date", "hold"):
            if key not in pos:
                fail(f"포트폴리오 포지션 키 누락({key}): {pos}")
        try:
            if float(pos.get("shares", 0)) <= 0 or float(pos.get("entry_price", 0)) <= 0:
                fail(f"포트폴리오 수량/가격 비정상: {pos.get('ticker')}")
        except (TypeError, ValueError):
            fail(f"포트폴리오 수치 형변환 실패: {pos.get('ticker')}")
    eq = st.get("equity") or {}
    if eq and positions:
        last_day = max(eq)
        if float(eq[last_day]) <= 0:
            fail("포트폴리오 평가액 0 이하")
    closed = st.get("closed") or []
    if closed:
        for c in closed[-5:]:
            if "exit_price" not in c:
                fail(f"청산 기록에 exit_price 없음: {c.get('ticker')}")


def check_overdue_positions() -> None:
    """보유기간이 지났는데 청산되지 않은 포지션(잡 미실행 신호)."""
    import sqlite3

    p = DATA / "disclosure_portfolio_state.json"
    db = DATA / "decisions.db"
    if not p.exists() or not db.exists():
        return
    st = json.loads(p.read_text(encoding="utf-8"))
    positions = st.get("positions") or []
    if not positions:
        return
    today = date.today().isoformat()
    try:
        con = sqlite3.connect(db)
        for pos in positions:
            t = str(pos.get("ticker"))
            rows = con.execute(
                "SELECT date FROM ohlcv WHERE ticker=? AND date>=? AND date<=? ORDER BY date",
                (t, str(pos.get("entry_date")), today),
            ).fetchall()
            days = [r[0] for r in rows]
            if days and len(days) - 1 >= int(pos.get("hold", 5)):
                warn(f"청산 지연 의심: {t} {pos.get('category')} (보유 {len(days)-1}일 ≥ {pos.get('hold')})")
        con.close()
    except Exception as e:
        warn(f"청산 지연 점검 실패: {e}")


def check_freshness() -> None:
    today = date.today()
    if today.weekday() >= 5:
        return
    state = DATA / "disclosure_watch_state.json"
    if state.exists():
        try:
            last = json.loads(state.read_text(encoding="utf-8")).get("last_run", "")
            if last and datetime.fromisoformat(last).date() < today - timedelta(days=3):
                warn(f"공시 감시 마지막 실행 {last} — 3일 이상 경과")
        except Exception:
            warn("공시 감시 상태 파일 해석 실패")

    sig = load_jsonl(DATA / "signal_log.jsonl")
    if sig:
        last_day = max(str(r.get("date")) for r in sig)
        if last_day < (today - timedelta(days=5)).isoformat():
            warn(f"시장 신호 기록 마지막 {last_day} — 5일 이상 경과")


def check_job_count() -> None:
    src = ROOT / "app" / "bootstrap.py"
    if not src.exists():
        return
    text = src.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        fail(f"bootstrap.py 구문 오류: {e}")
        return
    declared = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Subscript) and isinstance(node.value, ast.Constant):
                declared = node.value.value
    calls = text.count("add_job_with_retry(")
    if declared is not None and calls != declared:
        fail(f"job_count 선언({declared}) ≠ add_job_with_retry 호출({calls})")


def check_bom() -> None:
    bad = []
    for p in ROOT.rglob("*.py"):
        if any(part in {"_archive", ".git", "__pycache__"} for part in p.parts):
            continue
        with open(p, "rb") as f:
            if f.read(3) == b"\xef\xbb\xbf":
                bad.append(str(p.relative_to(ROOT)))
    if bad:
        fail(f"BOM 포함 파일 {len(bad)}개: {bad[:3]}")


def check_tracked_secrets() -> None:
    env = ROOT / ".env"
    if not (ROOT / ".git").exists():
        return
    if env.exists():
        try:
            import subprocess

            out = subprocess.run(
                ["git", "ls-files", ".env"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
            ).stdout.strip()
            if out:
                fail(".env가 git에 추적됨")
        except Exception:
            pass


def main() -> int:
    check_jsonl()
    check_portfolio()
    check_overdue_positions()
    check_freshness()
    check_job_count()
    check_bom()
    check_tracked_secrets()

    for w in WARNS:
        print(f"[WARN] {w}")
    for f in FAILS:
        print(f"[FAIL] {f}")
    if not FAILS and not WARNS:
        print("[OK] 자체 점검 통과")
    elif not FAILS:
        print(f"[OK] 오류 없음 · 경고 {len(WARNS)}건")
    else:
        print(f"[FAIL] 오류 {len(FAILS)}건 · 경고 {len(WARNS)}건")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
