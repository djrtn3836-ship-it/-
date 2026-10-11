"""재무/밸류에이션 데이터 제공 (2026-10-10 Session 107).

왜 필요한가
----------
DART 재무(ROE·부채비율·영업이익률)는 `/재무` 명령에서만 쓰였고 **판단에는
전혀 들어가지 않았다**. 재무는 가격 파생 팩터와 달리 "기업 자체 정보"다.

정직한 한계
----------
DART 재무제표에는 주가·주식수가 없어 **PER/PBR 같은 밸류에이션은 계산할 수
없다** — 수익성(ROE·영업이익률)과 안정성(부채비율)만 다룬다(추정 금지).

설계
----
- 7일 TTL 파일 캐시(분기 공시 데이터 — 자주 바뀌지 않음) → DART 레이트리밋 보호
- 동기 DART 호출은 `asyncio.to_thread`로 실행(이벤트 루프 차단 금지)
- 실패는 전부 {} 반환(예외 없음) — 본류 보호
- 점수는 바꾸지 않는다(태그 기록 → 승률 검증 후 반영)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("fundamentals")

# 임계값 (근거: 적자·과다부채는 상승 신호의 함정 확률이 높다 — 문서화된 규칙)
DEBT_RISKY = 200.0      # 부채비율 200% 이상 → 재무 리스크
ROE_WEAK = -10.0        # ROE -10% 이하 → 자본 훼손
OP_MARGIN_WEAK = -10.0  # 영업이익률 -10% 이하 → 사실상 적자
ROE_SOLID = 10.0        # ROE 10% 이상 + 부채 100% 이하 → 건실
DEBT_SOLID = 100.0


def fundamentals_enabled() -> bool:
    """재무 팩터 사용 여부(기본 활성 — 7일 캐시로 저렴)."""
    return os.getenv("NONPRICE_FUND_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _cache_path() -> Path:
    return Path(os.getenv("FUNDAMENTALS_CACHE_PATH", "data/fundamentals_cache.json"))


def _ttl_days() -> float:
    try:
        return max(0.1, float(os.getenv("FUNDAMENTALS_TTL_DAYS", "7")))
    except ValueError:
        return 7.0


def classify(f: Dict[str, Any]) -> Dict[str, Any]:
    """재무 스냅샷 → 태그/리스크 플래그 (순수 함수 — 테스트 대상).

    데이터가 없는 항목은 판단하지 않는다(추정 금지).
    """
    tags: List[str] = []
    notes: List[str] = []
    risk = False

    def _num(v: Any) -> Optional[float]:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    debt = _num(f.get("debt_ratio"))
    roe = _num(f.get("roe"))
    opm = _num(f.get("op_margin"))
    op_profit = _num(f.get("operating_profit"))
    net_profit = _num(f.get("net_profit"))

    if debt is not None and debt >= DEBT_RISKY:
        risk = True
        notes.append(f"부채비율 {debt:.0f}%")
    if roe is not None and roe <= ROE_WEAK:
        risk = True
        notes.append(f"ROE {roe:.1f}%")
    if opm is not None and opm <= OP_MARGIN_WEAK:
        risk = True
        notes.append(f"영업이익률 {opm:.1f}%")
    if (op_profit is not None and op_profit <= 0) or (
        net_profit is not None and net_profit <= 0
    ):
        risk = True
        if "적자" not in " ".join(notes):
            notes.append("적자(손실)")

    if risk:
        tags.append("fund_risky")

    if (roe is not None and roe >= ROE_SOLID) and (
        debt is not None and debt <= DEBT_SOLID
    ):
        tags.append("fund_solid")

    if not tags:
        tags.append("fund_ok")

    return {"tags": tags, "risk": risk, "notes": notes}


def _load_cache() -> Dict[str, Any]:
    p = _cache_path()
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.debug(f"재무 캐시 읽기 실패(무시): {e}")
    return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    try:
        p = _cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.debug(f"재무 캐시 저장 실패(무시): {e}")


def _fetch_sync(dart: Any, ticker: str) -> Optional[Dict[str, Any]]:
    """DART 동기 호출(스레드에서 실행). 실패 시 None."""
    corp_code = dart.get_corp_code_sync(ticker)
    if not corp_code:
        return None
    year = datetime.now().year - 1
    fin = dart.get_financials_sync(corp_code, str(year))
    if not fin:
        fin = dart.get_financials_sync(corp_code, str(year - 1))
    if not fin:
        return None
    out: Dict[str, Any] = {}
    for key, src in (
        ("revenue", "매출액"),
        ("operating_profit", "영업이익"),
        ("net_profit", "당기순이익"),
        ("roe", "ROE"),
        ("debt_ratio", "부채비율"),
        ("op_margin", "영업이익률"),
    ):
        v = fin.get(src)
        if v is None:
            continue  # 결측은 결측대로 둔다 — 0으로 위장 금지
        try:
            out[key] = float(v)
        except (TypeError, ValueError):
            continue
    return out or None


async def get_fundamentals(ticker: str, dart: Any) -> Dict[str, Any]:
    """재무 스냅샷(7일 캐시). 실패/미지원 시 {}."""
    if dart is None or not ticker:
        return {}

    cache = _load_cache()
    entry = cache.get(ticker)
    if isinstance(entry, dict):
        age_days = (time.time() - float(entry.get("ts", 0))) / 86400.0
        if age_days <= _ttl_days() and isinstance(entry.get("data"), dict):
            return dict(entry["data"])

    try:
        data = await asyncio.to_thread(_fetch_sync, dart, ticker)
    except Exception as e:
        logger.debug(f"[{ticker}] 재무 조회 실패(무시): {e}")
        return {}

    if not data:
        return {}

    cache[ticker] = {"ts": time.time(), "data": data}
    _save_cache(cache)
    return data
