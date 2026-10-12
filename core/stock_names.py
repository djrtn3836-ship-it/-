"""종목명 조회(알림 가독성) — KRX 유니버스 CSV 기반 경량 로더 + mtime 캐시.

2026-10-12 사용자 피드백: 텔레그램 신호가 숫자 코드(예: 005930)로만 표시돼
어떤 종목인지 즉시 알 수 없다 → "삼성전자(005930)" 형태로 표시한다.

- 소스: data/krx_universe.csv (code,name,market — universe_refresh가 토요일 갱신)
- mtime 캐시: 파일이 갱신되면 자동 재로딩(별도 refresh 호출 불필요)
- 실패 시 무해: 이름을 못 찾으면 입력 코드를 그대로 반환(알림은 계속 발송)
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

_cache: Dict[str, str] = {}
_key: Optional[Tuple[str, float]] = None


def _csv_path() -> Path:
    env = os.getenv("STOCK_NAMES_CSV")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "data" / "krx_universe.csv"


def _load() -> None:
    global _cache, _key
    path = _csv_path()
    try:
        mt = path.stat().st_mtime
    except OSError:
        _cache, _key = {}, None
        return
    if _key == (str(path), mt):
        return  # 변경 없음
    names: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                code = str(row.get("code") or "").strip().zfill(6)
                nm = str(row.get("name") or "").strip()
                if code and nm:
                    names[code] = nm
    except Exception:
        return
    if names:
        _cache, _key = names, (str(path), mt)


def stock_name(ticker: str, default: Optional[str] = None) -> str:
    """종목코드 → 종목명. 못 찾으면 default(없으면 입력값 그대로)."""
    _load()
    t = str(ticker or "").strip().zfill(6)
    nm = _cache.get(t)
    if nm:
        return nm
    return default if default is not None else str(ticker or "")
