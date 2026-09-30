# -*- coding: utf-8 -*-
"""tests/unit/test_universe_fetcher.py - 동적 유니버스 수집기 검증 (오프라인).

네이버 모바일 JSON API 응답 파싱/집계/저장을 검증한다.
"""

from pathlib import Path
from typing import Any, Dict

from scheduler.universe_fetcher import (
    FetchResult,
    UniverseEntry,
    UniverseFetcher,
    parse_market_json,
    save_universe_csv,
)

_PAYLOAD: Dict[str, Any] = {
    "totalCount": 900,
    "stocks": [
        {"itemCode": "005930", "stockName": "삼성전자", "stockEndType": "stock"},
        {"itemCode": "000660", "stockName": "SK하이닉스", "stockEndType": "stock"},
        {"itemCode": "005930", "stockName": "삼성전자", "stockEndType": "stock"},  # 중복
        {"itemCode": "105560", "stockName": "KB금융", "stockEndType": "stock"},
        {"itemCode": "069500", "stockName": "KODEX 200", "stockEndType": "etf"},   # ETF 제외
        {"itemCode": "BAD", "stockName": "잘못", "stockEndType": "stock"},          # 코드 형식 오류
    ],
}


class TestParse:
    def test_extracts_and_filters(self) -> None:
        entries = parse_market_json(_PAYLOAD, "KOSPI")
        codes = [e.code for e in entries]
        assert codes == ["005930", "000660", "005930", "105560"]  # ETF/오류 제외(중복은 상위에서 제거)
        assert entries[0].name == "삼성전자"
        assert entries[0].market == "KOSPI"

    def test_empty_payload(self) -> None:
        assert parse_market_json({}, "KOSPI") == []
        assert parse_market_json({"stocks": []}, "KOSPI") == []


class TestSave:
    def test_writes_header_and_rows_without_bom(self, tmp_path: Path) -> None:
        entries = parse_market_json(_PAYLOAD, "KOSPI")
        out = tmp_path / "u.csv"
        count = save_universe_csv(entries, out)

        assert count == len(entries)
        raw = out.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")  # BOM 없음
        text = raw.decode("utf-8")
        assert text.splitlines()[0] == "code,name,market"
        assert "005930,삼성전자,KOSPI" in text

    def test_sanitizes_commas_in_name(self, tmp_path: Path) -> None:
        out = tmp_path / "u.csv"
        save_universe_csv([UniverseEntry("005930", "삼성,전자", "KOSPI")], out)
        assert "005930,삼성 전자,KOSPI" in out.read_text(encoding="utf-8")


class TestFetcher:
    def _fetcher(self, page_size_full: int = 3):
        """page1은 꽉 찬 페이지, page2는 부분 페이지(종료 유도)."""
        def fetch(market: str, page: int, page_size: int) -> Dict[str, Any]:
            if page == 1:
                base = {"KOSPI": "0", "KOSDAQ": "5"}[market]
                return {
                    "stocks": [
                        {"itemCode": f"{base}0000{i}", "stockName": f"{market}종목{i}", "stockEndType": "stock"}
                        for i in range(page_size)
                    ]
                }
            if page == 2:
                base = {"KOSPI": "0", "KOSDAQ": "5"}[market]
                return {"stocks": [{"itemCode": f"{base}99999", "stockName": f"{market}추가", "stockEndType": "stock"}]}
            return {"stocks": []}
        return fetch

    def test_aggregates_markets_and_dedups(self) -> None:
        fetcher = UniverseFetcher(fetcher=self._fetcher(), delay=0, page_size=3)
        result = fetcher.fetch(pages=3)

        assert len(result.entries) == 8   # KOSPI 3 + 1 + KOSDAQ 3 + 1
        codes = [e.code for e in result.entries]
        assert len(codes) == len(set(codes))
        assert {e.market for e in result.entries} == {"KOSPI", "KOSDAQ"}
        assert "유니버스 8종목" in result.summary()

    def test_handles_fetch_error(self) -> None:
        def boom(market: str, page: int, page_size: int) -> Dict[str, Any]:
            raise RuntimeError("network down")

        assert UniverseFetcher(fetcher=boom, delay=0).fetch(pages=1).entries == []

    def test_result_as_dict(self) -> None:
        r = FetchResult(entries=parse_market_json(_PAYLOAD, "KOSPI"))
        assert r.as_dict()["000660"] == "SK하이닉스"
