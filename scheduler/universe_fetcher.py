# -*- coding: utf-8 -*-
"""scheduler/universe_fetcher.py - 동적 유니버스 수집기 (네이버 금융 시가총액 API).

배경:
    `infrastructure/market_data/universe_provider.py`는 `data/krx_universe.csv`가
    없으면 하드코딩 238종목으로 폴백한다(신규상장/시총변동 미반영 + 매 부팅 CRITICAL 경고).
    본 모듈은 네이버 금융의 **모바일 JSON API**에서 실제 시가총액 순위를 수집해 CSV로 저장한다.

    ※ 2026-09 기준 finance.naver.com 은 SPA로 전환되어 HTML 파싱이 불가하므로
      `m.stock.naver.com/api/stocks/marketValue/{KOSPI|KOSDAQ}` JSON API를 사용한다.

사용:
    python -m scheduler.universe_fetcher --pages 4 --out data/krx_universe.csv
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, Tuple

from core.logger import setup_logger

logger = setup_logger("universe_fetcher")

API_URL = "https://m.stock.naver.com/api/stocks/marketValue/{market}"
MARKETS: Tuple[str, ...] = ("KOSPI", "KOSDAQ")
DEFAULT_OUT = Path(__file__).parent.parent / "data" / "krx_universe.csv"
PAGE_SIZE = 100


@dataclass
class UniverseEntry:
    code: str
    name: str
    market: str


@dataclass
class FetchResult:
    entries: List[UniverseEntry] = field(default_factory=list)

    def as_dict(self) -> Dict[str, str]:
        return {e.code: e.name for e in self.entries}

    def summary(self) -> str:
        by_market: Dict[str, int] = {}
        for e in self.entries:
            by_market[e.market] = by_market.get(e.market, 0) + 1
        parts = ", ".join(f"{m} {n}" for m, n in sorted(by_market.items()))
        return f"유니버스 {len(self.entries)}종목 ({parts})"


def parse_market_json(payload: Dict[str, Any], market: str) -> List[UniverseEntry]:
    """시가총액 API 응답에서 (코드, 이름) 목록을 추출한다.

    - itemCode가 6자리 숫자인 것만 채택
    - stockEndType == "stock" (ETF/ETN 등 제외)
    """
    entries: List[UniverseEntry] = []
    for item in (payload or {}).get("stocks", []) or []:
        code = str(item.get("itemCode", "")).strip()
        name = str(item.get("stockName", "")).strip()
        end_type = str(item.get("stockEndType", "stock"))
        if not (len(code) == 6 and code.isdigit()):
            continue
        if not name or "<" in name or ">" in name:
            continue
        if end_type and end_type != "stock":
            continue
        entries.append(UniverseEntry(code=code, name=name, market=market))
    return entries


def _default_fetch(market: str, page: int, page_size: int = PAGE_SIZE) -> Dict[str, Any]:
    """네이버 모바일 시가총액 API 호출(JSON)."""
    import requests

    resp = requests.get(
        API_URL.format(market=market),
        params={"page": page, "pageSize": page_size},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


class UniverseFetcher:
    """시가총액 상위 종목을 수집해 유니버스를 만든다."""

    def __init__(
        self,
        fetcher: Callable[[str, int, int], Dict[str, Any]] | None = None,
        delay: float = 0.3,
        page_size: int = PAGE_SIZE,
    ) -> None:
        self._fetch = fetcher or _default_fetch
        self._delay = delay
        self._page_size = page_size

    def fetch(self, pages: int = 4) -> FetchResult:
        result = FetchResult()
        seen: set[str] = set()
        for market in MARKETS:
            for page in range(1, pages + 1):
                try:
                    payload = self._fetch(market, page, self._page_size)
                except Exception as e:
                    logger.warning(f"{market} p{page} 조회 실패: {e}")
                    break
                entries = parse_market_json(payload, market)
                raw_count = len((payload or {}).get("stocks", []) or [])
                new = [e for e in entries if e.code not in seen]
                for e in new:
                    seen.add(e.code)
                result.entries.extend(new)
                logger.info(f"{market} p{page}: {len(entries)}종목 (신규 {len(new)}, 누적 {len(result.entries)})")
                # 페이지 종료 판정은 '필터 전 원시 개수' 기준(ETF 제외로 조기 종료 방지)
                if raw_count < self._page_size:
                    break
                if self._delay > 0:
                    time.sleep(self._delay)
        logger.info(result.summary())
        return result


def save_universe_csv(entries: Sequence[UniverseEntry], out_path: Path = DEFAULT_OUT) -> int:
    """유니버스 CSV 저장(헤더: code,name,market). UTF-8 (BOM 없음)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write("code,name,market\n")
        for e in entries:
            safe_name = e.name.replace(",", " ").replace("\n", " ").strip()
            f.write(f"{e.code},{safe_name},{e.market}\n")
    logger.info(f"유니버스 CSV 저장: {out_path} ({len(entries)}종목)")
    return len(entries)


def _main(argv: Sequence[str] | None = None) -> int:
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="동적 유니버스 수집(네이버 금융 시가총액 API)")
    parser.add_argument("--pages", type=int, default=4, help="시장별 페이지 수(1페이지=100종목)")
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUT), help="출력 CSV 경로")
    args = parser.parse_args(list(argv) if argv is not None else None)

    result = UniverseFetcher().fetch(pages=args.pages)
    if not result.entries:
        print("수집 실패: 종목 없음 (네트워크/API 변경 확인)")
        return 1
    count = save_universe_csv(result.entries, Path(args.out))
    print(f"{result.summary()} → {args.out}")
    return 0 if count >= 50 else 1


if __name__ == "__main__":
    raise SystemExit(_main())
