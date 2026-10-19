# -*- coding: utf-8 -*-
"""공시 본문 파싱 (S143) — 자기주식취득 비율·소각 계획 추출.

DART document.xml(ZIP) → 텍스트 → '취득예정주식수/발행주식총수' → 비율.
"""
from __future__ import annotations

import html
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

import requests

BASE_URL = os.getenv("DART_API_URL", "https://opendart.fss.or.kr/api")
ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "buyback_ratios.json"

_NUM = r"([\d]{1,3}(?:,\d{3})+|\d+)"


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


def fetch_text(rcept_no: str, api_key: str, *, session: Optional[requests.Session] = None,
               timeout: int = 30) -> str:
    """document.xml(ZIP) → 태그 제거 텍스트."""
    sess = session or requests.Session()
    try:
        sess.headers.update({"User-Agent": "Mozilla/5.0"})
    except AttributeError:
        pass
    resp = sess.get(f"{BASE_URL}/document.xml", params={"crtfc_key": str(api_key), "rcept_no": str(rcept_no)}, timeout=timeout)
    resp.raise_for_status()
    if not resp.content[:2] == b"PK":
        raise RuntimeError(f"문서가 ZIP이 아님(rcept_no={rcept_no})")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    parts = [
        zf.read(n).decode("utf-8", errors="ignore")
        for n in zf.namelist()
        if n.lower().endswith((".xml", ".html", ".htm", ".txt"))
    ]
    text = html.unescape(re.sub(r"<[^>]+>", " ", " ".join(parts)))
    return re.sub(r"\s+", " ", text)


def _num_after(text: str, label: str, span: int = 160) -> Optional[int]:
    """label 뒤 첫 숫자(보통주 우선)."""
    for m in re.finditer(label, text):
        seg = text[m.end():m.end() + span]
        mm = re.search(r"보통주[^\d]{0,15}" + _NUM, seg) or re.search(_NUM, seg)
        if mm:
            try:
                return int(mm.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def parse_buyback(text: str) -> Dict[str, Any]:
    """자기주식취득 본문 → 취득주식수·취득금액·비율·소각계획.

    DART 표기는 '취득예정주식(주)' (수 없음). 발행주식총수는 표에 없고
    '발행주식총수의 1% : 보통주 N주' 주석에서 역산한다(유통주식 기준).
    """
    buy = _num_after(text, r"취득예정주식")
    amount = _num_after(text, r"취득예정금액")
    total = None
    m = re.search(r"발행주식총수의?\s*1\s*%\s*[:：]?\s*보통주\s*" + _NUM, text)
    if m:
        try:
            total = int(m.group(1).replace(",", "")) * 100
        except ValueError:
            total = None
    ratio = None
    if buy and total and 0 < buy < total:
        ratio = round(buy / total * 100.0, 4)
    return {
        "buy_shares": buy,
        "buy_amount": amount,
        "float_shares": total,
        "ratio_pct": ratio,
        "cancel_flag": ("소각" in text),
    }


def get_buyback(rcept_no: str, api_key: Optional[str] = None, *,
                cache_path: Path = CACHE_PATH,
                session: Optional[requests.Session] = None,
                sleep: float = 0.12,
                logger: Any = None) -> Dict[str, Any]:
    """캐시 우선 문서 파싱. 실패는 {"error": ...}로 반환."""
    cache: Dict[str, Any] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    if str(rcept_no) in cache:
        return cache[str(rcept_no)]

    key = api_key or _api_key()
    if not key:
        return {"error": "no_key"}
    try:
        text = fetch_text(rcept_no, key, session=session)
        res = parse_buyback(text)
        res["ok"] = True
    except Exception as e:
        res = {"ok": False, "error": str(e)}
        if logger:
            logger.warning(f"본문 파싱 실패({rcept_no}): {e}")
    cache[str(rcept_no)] = res
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    if sleep:
        time.sleep(sleep)
    return res
