# -*- coding: utf-8 -*-
"""공시 일일 감시 (S141) — DART 전체 공시 → 유니버스 필터 → A/B 이벤트 기록·알림.

- 매일 08:10(전일 야간분 보완) · 16:40(당일 마감 후) 2회, 최근 2일을 스캔하고 로그로 중복 제거.
- A급(무상증자)은 텔레그램 알림, A/B는 브리핑·대시보드 표시. 실제 주문 없음.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

BASE_URL = os.getenv("DART_API_URL", "https://opendart.fss.or.kr/api")
ROOT = Path(__file__).resolve().parent.parent
WATCH_LOG = ROOT / "data" / "disclosure_watch_log.jsonl"
STATE_PATH = ROOT / "data" / "disclosure_watch_state.json"
STUDY_PATH = ROOT / "data" / "disclosure_study.json"
ENTRY_STUDY_PATH = ROOT / "data" / "disclosure_entry_study.json"
PAGE_COUNT = 100
DEFAULT_DAYS = 2
SCAN_MAX_PAGES = 60


def _api_key() -> str:
    key = os.getenv("DART_API_KEY", "")
    if not key:
        try:
            from dotenv import load_dotenv

            load_dotenv(ROOT / ".env")
        except Exception:
            pass
        key = os.getenv("DART_API_KEY", "")
    return key


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def fetch_day(
    day: str,
    api_key: str,
    *,
    session: Optional[requests.Session] = None,
    sleep: float = 0.12,
    max_pages: int = SCAN_MAX_PAGES,
) -> List[Dict[str, Any]]:
    """하루치 전체 공시를 페이지 루프로 수집 → 상장 종목(6자리 코드)만 반환."""
    sess = session or requests.Session()
    try:
        sess.headers.update({"User-Agent": "Mozilla/5.0"})
    except AttributeError:
        pass
    out: List[Dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        params = {
            "crtfc_key": str(api_key),
            "bgn_de": str(day),
            "end_de": str(day),
            "page_no": str(page),
            "page_count": str(PAGE_COUNT),
        }
        try:
            resp = sess.get(f"{BASE_URL}/list.json", params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"DART 전체 공시 조회 실패({day} p{page}): {e}") from e
        status = str(data.get("status", ""))
        if status == "013":
            break
        if status != "000":
            raise RuntimeError(f"DART status={status} ({data.get('message')})")
        items = data.get("list") or []
        for it in items:
            code = str(it.get("stock_code") or "").strip()
            if len(code) != 6 or not code.isdigit():
                continue
            out.append(
                {
                    "date": str(it.get("rcept_dt") or day),
                    "ticker": code,
                    "title": str(it.get("report_nm") or "").strip(),
                    "type": str(it.get("pblntf_ty") or ""),
                }
            )
        if page >= int(data.get("total_page") or 1) or not items:
            break
        page += 1
        if sleep:
            time.sleep(sleep)
    return out


def load_study_stats(study_path: Path = STUDY_PATH) -> Dict[str, Dict[str, Any]]:
    """공시 연구 결과 → {유형: {grade, mean5, hit5, n5}}."""
    try:
        study = json.loads(study_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for cat, st in (study.get("categories") or {}).items():
        hs = (st.get("horizons") or {}).get("5") or {}
        out[cat] = {
            "grade": st.get("grade"),
            "mean5": hs.get("mean"),
            "hit5": hs.get("hit"),
            "n5": hs.get("n"),
        }
    return out


def _scan_dates(days: int = DEFAULT_DAYS, today: Optional[datetime] = None) -> List[str]:
    """최근 N일(평일만) YYYYMMDD 목록."""
    now = today or datetime.now()
    out: List[str] = []
    for i in range(max(days, 1)):
        d = now - timedelta(days=i)
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
    return sorted(out)


def run_watch(
    days: int = DEFAULT_DAYS,
    *,
    api_key: Optional[str] = None,
    session: Optional[requests.Session] = None,
    study_path: Path = STUDY_PATH,
    log_path: Path = WATCH_LOG,
    state_path: Path = STATE_PATH,
    send_alert: bool = True,
    logger: Any = None,
) -> Dict[str, Any]:
    """최근 N일 공시 스캔 → 신규 A/B/C 이벤트 기록(+A급 알림). 중복은 로그로 제거."""
    from validation.disclosure_event_study import classify_title

    key = api_key or _api_key()
    if not key:
        raise RuntimeError("DART_API_KEY 없음 (.env 확인)")

    from infrastructure.market_data.universe_provider import get_universe

    universe = get_universe()
    stats = load_study_stats(study_path)
    dates = _scan_dates(days)
    known = {
        (str(r.get("date")), str(r.get("ticker")), str(r.get("title")))
        for r in _load_jsonl(log_path)
    }

    fetched = 0
    new_events: List[Dict[str, Any]] = []
    for day in dates:
        rows = fetch_day(day, key, session=session)
        fetched += len(rows)
        for r in rows:
            cat = classify_title(r["title"])
            if not cat or r["ticker"] not in universe:
                continue
            st = stats.get(cat) or {}
            if not st or str(st.get("grade") or "").startswith("표본"):
                continue
            rec = {
                **r,
                "name": universe.get(r["ticker"], ""),
                "category": cat,
                "grade": st.get("grade"),
                "mean5": st.get("mean5"),
                "hit5": st.get("hit5"),
                "n5": st.get("n5"),
                "logged_at": datetime.now().isoformat(timespec="seconds"),
            }
            if (rec["date"], rec["ticker"], rec["title"]) in known:
                continue
            known.add((rec["date"], rec["ticker"], rec["title"]))
            new_events.append(rec)

    if new_events:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            for rec in new_events:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    a_grade = [e for e in new_events if str(e.get("grade") or "").startswith("A")]
    alerted = False
    if a_grade and send_alert:
        try:
            import asyncio

            from report.telegram_sender import TelegramSender

            text = format_alert(a_grade)
            asyncio.run(TelegramSender().send_raw(text))
            alerted = True
        except Exception as e:
            if logger:
                logger.warning(f"공시 신호 알림 실패(무시): {e}")

    state = {
        "last_run": datetime.now().isoformat(timespec="seconds"),
        "last_dates": dates,
        "last_new": len(new_events),
        "last_a": len(a_grade),
    }
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    if logger:
        logger.info(
            f"공시 감시 {dates} | 공시 {fetched}건 | 신규 {len(new_events)}건 | A급 {len(a_grade)}건"
        )
    return {
        "status": "ok",
        "dates": dates,
        "fetched": fetched,
        "new": len(new_events),
        "a_grade": len(a_grade),
        "alerted": alerted,
        "events": new_events,
    }


def load_entry_stats(entry_path: Path = ENTRY_STUDY_PATH) -> Dict[str, Dict[str, Any]]:
    """진입 실증(S142) → {유형: {e2_mean, e2_n, e1_mean}}."""
    try:
        data = json.loads(entry_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for cat, res in (data or {}).items():
        if not isinstance(res, dict) or "entries" not in res:
            continue
        entries = res.get("entries") or {}
        e2 = entries.get("E2_5") or {}
        e1 = entries.get("E1_5") or {}
        out[cat] = {"e2_mean": e2.get("mean"), "e2_n": e2.get("n"), "e1_mean": e1.get("mean")}
    return out


def format_alert(events: List[Dict[str, Any]], entry_stats: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    lines = ["📢 <b>공시 신호 (A급)</b>"]
    stats = entry_stats if entry_stats is not None else load_entry_stats()
    for e in events[:6]:
        lines.append(f"• <b>{e.get('category')}</b>: {e.get('name')}({e.get('ticker')})")
        if e.get("mean5") is not None:
            lines.append(
                f"  역사 5일 평균 {float(e['mean5']):+.2f}% · 적중 {float(e.get('hit5') or 0):.0f}% "
                f"(n={e.get('n5')})"
            )
        es = stats.get(str(e.get("category") or "")) or {}
        if es.get("e2_mean") is not None:
            e2 = float(es["e2_mean"])
            lines.append(f"  다음날 시가 진입 5일 {e2:+.2f}% (n={es.get('e2_n')})")
            if e2 < 1.0:
                lines.append("  ※ 효과가 당일 소멸 — 14:50 장중 알림에서만 유효")
    lines.append("※ 공시일 종가 매수 가정 · 5일 보유 권장(손절 -3%는 역효과 실증) · 실제 주문 없음")
    return "\n".join(lines)


def events_for_day(
    day: str,
    *,
    log_path: Path = WATCH_LOG,
    grades: tuple = ("A_생존", "B_경계"),
) -> List[Dict[str, Any]]:
    """해당일(YYYYMMDD/YYYY-MM-DD) A/B 이벤트 — 종목·유형 중복 제거."""
    target = str(day).replace("-", "")
    seen = set()
    out: List[Dict[str, Any]] = []
    for r in _load_jsonl(log_path):
        if str(r.get("date")) != target:
            continue
        if str(r.get("grade")) not in grades:
            continue
        key = (str(r.get("ticker")), str(r.get("category")))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    out.sort(key=lambda r: (str(r.get("grade")), str(r.get("ticker"))))
    return out


def latest_event_day(log_path: Path = WATCH_LOG) -> str:
    """감시 로그의 가장 최근 날짜(YYYYMMDD). 없으면 빈 문자열."""
    rows = _load_jsonl(log_path)
    return max((str(r.get("date") or "") for r in rows), default="")


def described(e: Dict[str, Any]) -> str:
    base = f"{e.get('name')}({e.get('ticker')}) {e.get('category')}"
    if e.get("mean5") is not None:
        base += f" · 역사 5d {float(e['mean5']):+.2f}%/적중 {float(e.get('hit5') or 0):.0f}%"
    return base


async def scheduled_disclosure_watch() -> Dict[str, Any]:
    """스케줄러 진입점 — 실패해도 예외를 밖으로 던지지 않는다."""
    try:
        return run_watch(send_alert=True)
    except Exception as e:
        return {"status": "error", "error": str(e)}
