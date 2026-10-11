"""재무(펀더멘털) 판단 반영 테스트 (2026-10-10 Session 107).

실검증에서 발견한 실버그 2건의 회귀 방지가 핵심:
① DART corpCode.xml은 **ZIP**으로 내려온다(이전: XML만 기대 → 매핑 영구 실패)
② `sj_div != "CFS"` 필터는 오류 — sj_div는 BS/IS(재무제표 종류),
   연결/별도는 fs_div(CFS/OFS)다. 이 필터가 모든 항목을 버려 재무가 빈 값이었다.
③ 결측을 0.0으로 위장하면 **가짜 리스크**(적자 오판)가 생긴다 — 금지.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collector.fundamentals_provider import classify  # noqa: E402


class TestClassify:

    def test_solid_fundamentals(self):
        r = classify({"roe": 10.4, "debt_ratio": 29.9, "op_margin": 13.1,
                      "net_profit": 1e12, "operating_profit": 1e12})
        assert r["tags"] == ["fund_solid"] and r["risk"] is False

    def test_risky_debt(self):
        r = classify({"roe": 5.0, "debt_ratio": 250.0})
        assert "fund_risky" in r["tags"] and r["risk"] is True

    def test_risky_loss(self):
        r = classify({"net_profit": -500.0, "roe": 2.0, "debt_ratio": 50.0})
        assert r["risk"] is True and "적자" in " ".join(r["notes"])

    def test_missing_values_are_not_guessed(self):
        """결측(None/부재)은 판단하지 않는다 — 0으로 위장한 가짜 리스크 금지."""
        r = classify({"debt_ratio": 45.9, "op_margin": 48.6})  # ROE·순이익 없음
        assert r["risk"] is False and r["tags"] == ["fund_ok"]

    def test_zero_net_profit_key_absent(self):
        """provider가 결측을 아예 넣지 않으면 0 판정도 일어나지 않아야 한다."""
        r = classify({"roe": 35.6, "debt_ratio": 45.9, "op_margin": 48.6})
        assert r["risk"] is False


class _FakeDart:
    def __init__(self, fin, corp="00126380"):
        self._fin = fin
        self._corp = corp
        self.calls = []

    def get_corp_code_sync(self, ticker):
        return self._corp

    def get_financials_sync(self, corp_code, year):
        self.calls.append(year)
        return dict(self._fin)


class TestProvider:

    def _get(self, tmp_path, fin, monkeypatch):
        monkeypatch.setenv("FUNDAMENTALS_CACHE_PATH", str(tmp_path / "fc.json"))
        from collector.fundamentals_provider import get_fundamentals

        return asyncio.run(get_fundamentals("005930", _FakeDart(fin)))

    def test_partial_data_keeps_none_out(self, tmp_path, monkeypatch):
        # 부채비율은 커넥터(dart_connector)가 계산해 넘긴다 — 여기선 계산 결과를 흉내낸다
        f = self._get(
            tmp_path,
            {"매출액": 333.6e12, "영업이익": 43.6e12, "부채비율": 29.9},
            monkeypatch,
        )
        assert "revenue" in f and "operating_profit" in f and f["debt_ratio"] == 29.9
        assert "net_profit" not in f and "roe" not in f  # 결측은 키 자체가 없다

    def test_empty_financials_returns_empty(self, tmp_path, monkeypatch):
        assert self._get(tmp_path, {}, monkeypatch) == {}

    def test_cache_avoids_refetch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUNDAMENTALS_CACHE_PATH", str(tmp_path / "fc.json"))
        from collector.fundamentals_provider import get_fundamentals

        dart = _FakeDart({"매출액": 1e9, "영업이익": 1e8})
        f1 = asyncio.run(get_fundamentals("005930", dart))
        f2 = asyncio.run(get_fundamentals("005930", dart))
        assert f1 == f2 and len(dart.calls) == 1, "캐시가 동작하지 않음(재조회 발생)"

    def test_no_dart_returns_empty(self):
        from collector.fundamentals_provider import get_fundamentals

        assert asyncio.run(get_fundamentals("005930", None)) == {}


class TestRegressionGuards:

    def _dart_src(self) -> str:
        p = Path(__file__).resolve().parents[2] / "data" / "dart_connector.py"
        return p.read_text(encoding="utf-8")

    def test_zip_handling_present(self):
        src = self._dart_src()
        assert "zipfile" in src, "corpCode.xml ZIP 해제가 사라짐(재무 경로 사망)"

    def test_fs_div_not_sj_div(self):
        src = self._dart_src()
        assert 'sj_div") != "CFS"' not in src, "잘못된 sj_div 필터가 부활함"
        assert 'fs_div' in src, "fs_div(연결/별도) 기반 필터가 사라짐"

    def test_no_zero_fabrication(self):
        src = self._dart_src()
        assert 'result.get("매출액", 0.0)' not in src, "결측 0.0 위장이 부활함(가짜 리스크)"

    def test_nonprice_integration_wired(self):
        root = Path(__file__).resolve().parents[2]
        src = (root / "application" / "analysis" / "nonprice_factors.py").read_text(encoding="utf-8")
        assert "_fundamental_part" in src and "fund_tags" in src
        boot = (root / "app" / "bootstrap.py").read_text(encoding="utf-8")
        assert 'analysis["tags"]' in boot, "워커가 태그를 결정/모의매매로 전달하지 않음"
