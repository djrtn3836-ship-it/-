"""수급(외국인/기관) 수집·저장·태그 테스트 (2026-10-10 Session 106).

배경: 수급 API는 주간 PDF·온디맨드 명령에서만 쓰였고 판단에는 0 상수로 들어갔다.
→ 일별 수집 → DB 저장 → **태그 기록만**(행동 변경 없음, 검증 후 반영 원칙).
응답 구조가 문서와 다를 수 있어(실측 경고 이력) 파싱은 방어적이어야 하고,
모르면 **추정하지 않는다**(0으로 채우지 않음).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collector.supply_demand_collector import collect_supply_demand, extract_net  # noqa: E402


class TestExtractNet:

    def test_flat_shapes(self):
        assert extract_net({"net_buy": 1200}) == 1200.0
        assert extract_net({"net_buy_qty": "-340"}) == -340.0
        assert extract_net({"foreign_net": "1,250"}) == 1250.0
        assert extract_net({"inst_net": "+880"}) == 880.0

    def test_output_list_shape(self):
        assert extract_net({"output": [{"net_buy": 42}]}) == 42.0
        assert extract_net({"output": [{"trde_qty": "77"}]}) == 77.0

    def test_missing_or_error_returns_none(self):
        assert extract_net({"error": "401"}) is None
        assert extract_net({"output": []}) is None
        assert extract_net({"weird": 1}) is None
        assert extract_net("not-a-dict") is None
        assert extract_net(None) is None

    def test_no_guessing_with_bool(self):
        assert extract_net({"net_buy": True}) is None


class _FakeKiwoom:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    async def request_tr(self, ticker, tr_type, **kw):
        self.calls.append((ticker, tr_type))
        if not self.ok:
            return {"error": "no_server"}
        # 2026-10-13: 수급 소스가 일봉(ka10060) 레코드로 바뀜(외국인수급/기관수급 API는 미지원 ID)
        dt = str(kw.get("dt") or "20261013")
        return {
            "symbol": ticker,
            "date": f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}",
            "foreign_net": 100,
            "inst_net": -50,
            "close": 1000.0,
        }


class _FakeDB:
    def __init__(self):
        self.rows = []

    async def save_supply_demand(self, records):
        self.rows.extend(records)
        return len(records)


class TestCollector:

    def test_collects_both_sides(self):
        k, db = _FakeKiwoom(), _FakeDB()
        saved = asyncio.run(
            collect_supply_demand(k, db, ["005930", "000660"], date_str="2026-10-12", delay=0)
        )
        assert saved == 2
        assert ("005930", "일봉") in k.calls
        assert db.rows[0]["foreign_net"] == 100.0
        assert db.rows[0]["institution_net"] == -50.0
        assert db.rows[0]["date"] == "2026-10-12"

    def test_bar_date_mismatch_is_skipped(self):
        """응답 봉 날짜가 요청 날짜와 다르면 저장하지 않는다(오라벨 방지)."""

        class _OldBar(_FakeKiwoom):
            async def request_tr(self, ticker, tr_type, **kw):
                r = await super().request_tr(ticker, tr_type, **kw)
                r["date"] = "2026-10-08"
                return r

        k, db = _OldBar(), _FakeDB()
        saved = asyncio.run(
            collect_supply_demand(k, db, ["005930"], date_str="2026-10-12", delay=0)
        )
        assert saved == 0 and db.rows == []

    def test_failure_is_silent(self):
        k, db = _FakeKiwoom(ok=False), _FakeDB()
        saved = asyncio.run(
            collect_supply_demand(k, db, ["005930"], date_str="2026-10-12", delay=0)
        )
        assert saved == 0 and db.rows == []

    def test_no_inputs_no_error(self):
        assert asyncio.run(collect_supply_demand(None, None, [])) == 0

    def test_max_tickers_cap(self):
        k, db = _FakeKiwoom(), _FakeDB()
        tickers = [f"{i:06d}" for i in range(10)]
        saved = asyncio.run(
            collect_supply_demand(k, db, tickers, date_str="2026-10-12", delay=0, max_tickers=3)
        )
        assert saved == 3


class _FakeFlowDB:
    def __init__(self, rows):
        self._rows = rows

    async def get_supply_demand(self, ticker, days):
        return self._rows


class TestFlowTags:

    def _compute_tags(self, rows, **kw):
        from application.analysis.nonprice_factors import compute

        return asyncio.run(compute("005930", db=_FakeFlowDB(rows) if rows is not None else None, **kw))

    def test_both_buy_tag(self):
        rows = [
            {"foreign_net": 100, "institution_net": 50},
            {"foreign_net": 200, "institution_net": 80},
            {"foreign_net": 50, "institution_net": 10},
        ]
        f = self._compute_tags(rows)
        assert f.flow_foreign_5d == 350.0
        assert f.flow_inst_5d == 140.0
        assert "flow_f_buy" in f.tags and "flow_i_buy" in f.tags
        assert "flow_both_buy" in f.tags

    def test_sell_tags(self):
        f = self._compute_tags([{"foreign_net": -10, "institution_net": -20}])
        assert "flow_f_sell" in f.tags and "flow_i_sell" in f.tags
        assert "flow_both_buy" not in f.tags

    def test_no_data_no_tags(self):
        f = self._compute_tags([])
        assert f.flow_foreign_5d is None
        assert not any(t.startswith("flow_") for t in f.tags)

    def test_none_db_is_safe(self):
        f = self._compute_tags(None)
        assert not any(t.startswith("flow_") for t in f.tags)


class TestDbStorage:
    """CQRS 배치 커밋 설계: 저장 후 `_flush_pending()`으로 커밋해야 읽힌다."""

    def _run(self, tmp_path, fn):
        from data.db_manager import DatabaseManager

        db = DatabaseManager(db_path=tmp_path / "sd.db")

        async def run():
            try:
                await db.init_db()
                await fn(db)
            finally:
                await db.close()

        asyncio.run(run())

    def test_save_and_get_roundtrip(self, tmp_path):
        async def fn(db):
            n = await db.save_supply_demand([
                {"ticker": "005930", "date": "2026-10-10", "foreign_net": 100, "institution_net": 50},
                {"ticker": "005930", "date": "2026-10-12", "foreign_net": 200, "institution_net": -30},
                {"ticker": "000660", "date": "2026-10-12", "foreign_net": -10, "institution_net": 5},
            ])
            assert n == 3
            await db._flush_pending()
            rows = await db.get_supply_demand("005930", 5)
            assert [r["date"] for r in rows] == ["2026-10-12", "2026-10-10"]  # 최신순
            latest = await db.get_supply_demand_latest()
            assert len(latest) == 2  # 2026-10-12 두 종목

        self._run(tmp_path, fn)

    def test_upsert_same_key(self, tmp_path):
        async def fn(db):
            await db.save_supply_demand(
                [{"ticker": "005930", "date": "2026-10-12", "foreign_net": 100, "institution_net": 1}]
            )
            await db.save_supply_demand(
                [{"ticker": "005930", "date": "2026-10-12", "foreign_net": 999, "institution_net": 2}]
            )
            await db._flush_pending()
            rows = await db.get_supply_demand("005930", 5)
            assert len(rows) == 1 and rows[0]["foreign_net"] == 999.0

        self._run(tmp_path, fn)

    def test_missing_fields_skipped(self, tmp_path):
        async def fn(db):
            n = await db.save_supply_demand([
                {"ticker": "", "date": "2026-10-12"},
                {"ticker": "005930", "date": ""},
                {"ticker": "005930", "date": "2026-10-12", "foreign_net": None, "institution_net": None},
            ])
            assert n == 1  # 마지막 건만 저장(None은 '데이터 없음' — 0으로 위장하지 않음)
            await db._flush_pending()
            rows = await db.get_supply_demand("005930", 5)
            assert rows[0]["foreign_net"] is None

        self._run(tmp_path, fn)

    def test_latest_with_explicit_date(self, tmp_path):
        async def fn(db):
            await db.save_supply_demand([
                {"ticker": "005930", "date": "2026-10-10", "foreign_net": 1, "institution_net": 1},
                {"ticker": "000660", "date": "2026-10-12", "foreign_net": 2, "institution_net": 2},
            ])
            await db._flush_pending()
            rows = await db.get_supply_demand_latest("2026-10-10")
            assert len(rows) == 1 and rows[0]["ticker"] == "005930"

        self._run(tmp_path, fn)


class TestSchedulerRegistration:

    def test_job_registered(self):
        p = Path(__file__).resolve().parents[2] / "app" / "bootstrap.py"
        src = p.read_text(encoding="utf-8")
        assert '"supply_demand"' in src
        assert "CronTrigger(day_of_week=\"mon-fri\", hour=16, minute=35" in src
        assert 'self.startup_details["job_count"] = 28' in src

    def test_trading_day_guard(self):
        p = Path(__file__).resolve().parents[2] / "app" / "bootstrap.py"
        src = p.read_text(encoding="utf-8")
        i = src.index("async def _run_supply_demand")
        body = src[i : i + 600]
        assert "is_trading_day()" in body, "수급 수집에 거래일 가드가 없음"
