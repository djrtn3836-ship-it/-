# -*- coding: utf-8 -*-
"""공시 이력 백필 (S140) — DART list.json, 유니버스 종목별 5년.

출력: data/disclosures_history.json
  {"fetched_at": ..., "tickers": {"005930": {"name": "삼성전자", "events": [
      {"date": "20261015", "title": "주요사항보고서(자사주취득결정)", "type": "B"}, ...]}}}
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

BASE_URL = os.getenv("DART_API_URL", "https://opendart.fss.or.kr/api")
HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "disclosures_history.json"
PAGE_COUNT = 100
DEFAULT_SLEEP = 0.12

_STATUS_NO_DATA = "013"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    return s


def fetch_period(
    corp_code: str,
    bgn_de: str,
    end_de: str,
    api_key: str,
    *,
    session: Optional[requests.Session] = None,
    sleep: float = DEFAULT_SLEEP,
    max_pages: int = 20,
) -> List[Dict[str, Any]]:
    """한 기간(보통 1년)의 공시 목록을 페이지 루프로 수집."""
    sess = session or _session()
    rows: List[Dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        params = {
            "crtfc_key": str(api_key),
            "corp_code": str(corp_code),
            "bgn_de": str(bgn_de),
            "end_de": str(end_de),
            "page_no": str(page),
            "page_count": str(PAGE_COUNT),
        }
        try:
            resp = sess.get(f"{BASE_URL}/list.json", params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise RuntimeError(f"DART 호출 실패({corp_code} {bgn_de}~{end_de} p{page}): {e}") from e

        status = str(data.get("status", ""))
        if status == _STATUS_NO_DATA:
            break
        if status != "000":
            raise RuntimeError(f"DART status={status} ({data.get('message')}) corp={corp_code}")

        items = data.get("list") or []
        for it in items:
            rows.append(
                {
                    "date": str(it.get("rcept_dt") or ""),
                    "title": str(it.get("report_nm") or "").strip(),
                    "type": str(it.get("pblntf_ty") or ""),
                    "rcept_no": str(it.get("rcept_no") or ""),
                }
            )
        total_page = int(data.get("total_page") or 1)
        if page >= total_page or not items:
            break
        page += 1
        if sleep:
            time.sleep(sleep)
    return rows


def _dedupe(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for e in sorted(events, key=lambda x: (str(x.get("date")), str(x.get("title")))):
        key = (e.get("date"), e.get("title"))
        if key in seen or not e.get("date") or not e.get("title"):
            continue
        seen.add(key)
        out.append(e)
    return out


def fetch_ticker(
    ticker: str,
    corp_code: str,
    api_key: str,
    *,
    years: int = 5,
    session: Optional[requests.Session] = None,
    sleep: float = DEFAULT_SLEEP,
) -> List[Dict[str, Any]]:
    """종목 1개의 N년치 공시(연도 단위 분할)."""
    now = datetime.now()
    first_year = now.year - years + 1
    events: List[Dict[str, Any]] = []
    for y in range(first_year, now.year + 1):
        bgn = f"{y}0101"
        end = now.strftime("%Y%m%d") if y == now.year else f"{y}1231"
        events += fetch_period(corp_code, bgn, end, api_key, session=session, sleep=sleep)
    return _dedupe(events)


def backfill(
    years: int = 5,
    limit: Optional[int] = None,
    sleep: float = DEFAULT_SLEEP,
    *,
    history_path: Path = HISTORY_PATH,
    api_key: Optional[str] = None,
    logger: Any = None,
) -> Dict[str, Any]:
    """유니버스 전체 백필 → JSON 저장. 기존 파일은 병합(재수집은 덮어쓰기)."""
    from infrastructure.dart.client import DartConnector
    from infrastructure.market_data.universe_provider import get_universe

    key = api_key or os.getenv("DART_API_KEY", "")
    if not key:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass
        key = os.getenv("DART_API_KEY", "")
    if not key:
        raise RuntimeError("DART_API_KEY 없음 (.env 확인)")
    client = DartConnector(key)
    universe = get_universe()
    tickers = list(universe.items())
    if limit:
        tickers = tickers[:limit]

    out: Dict[str, Any] = {"fetched_at": "", "years": years, "tickers": {}}
    if history_path.exists():
        try:
            prev = json.loads(history_path.read_text(encoding="utf-8"))
            out["tickers"] = prev.get("tickers", {})
        except Exception:
            pass

    sess = _session()
    ok = fail = no_corp = 0
    for i, (ticker, name) in enumerate(tickers, 1):
        corp = client.get_corp_code_sync(ticker)
        if not corp:
            no_corp += 1
            if logger:
                logger.warning(f"[{i}/{len(tickers)}] {ticker} corp_code 없음")
            continue
        try:
            events = fetch_ticker(ticker, corp, key, years=years, session=sess, sleep=sleep)
            out["tickers"][ticker] = {"name": name, "events": events}
            ok += 1
            if logger and (i % 10 == 0 or i == len(tickers)):
                logger.info(f"[{i}/{len(tickers)}] {ticker} {name}: {len(events)}건 누적")
        except Exception as e:
            fail += 1
            if logger:
                logger.error(f"[{i}/{len(tickers)}] {ticker} 실패: {e}")
        if i % 20 == 0:
            out["fetched_at"] = datetime.now().isoformat(timespec="seconds")
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    out["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    total = sum(len(v.get("events", [])) for v in out["tickers"].values())
    return {"ok": ok, "fail": fail, "no_corp": no_corp, "tickers": len(out["tickers"]), "events": total}


def compact_history(history_path: Path = HISTORY_PATH, logger: Any = None) -> Dict[str, int]:
    """연구 대상 유형(분류 가능한 제목)만 남겨 파일 크기를 줄인다."""
    from validation.disclosure_event_study import classify_title

    data = json.loads(history_path.read_text(encoding="utf-8"))
    kept = dropped = 0
    for info in data.get("tickers", {}).values():
        events = []
        for e in info.get("events", []):
            if classify_title(e.get("title", "")):
                events.append(e)
                kept += 1
            else:
                dropped += 1
        info["events"] = events
    history_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if logger:
        logger.info(f"compact: kept={kept} dropped={dropped}")
    return {"kept": kept, "dropped": dropped}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    ap.add_argument("--compact", action="store_true", help="연구 대상 유형만 남기기")
    args = ap.parse_args()
    from core.logger import setup_logger

    logger = setup_logger("disclosure_backfill")
    result = backfill(years=args.years, limit=args.limit, sleep=args.sleep, logger=logger)
    if args.compact:
        result["compact"] = compact_history(logger=logger)
    print("RESULT", result)


if __name__ == "__main__":
    main()
