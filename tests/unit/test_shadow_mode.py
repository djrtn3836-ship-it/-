# -*- coding: utf-8 -*-
"""
tests/unit/test_shadow_mode.py - Shadow Mode 테스트 (v1.0)

테스트 클래스:
    TestShadowRecord            (7개) : DTO 생성 및 to_dict()
    TestShadowRunnerInit        (4개) : ShadowRunner 초기화 검증
    TestShadowRunnerRun         (9개) : 정상/에러/타임아웃 실행
    TestShadowEvaluatorRecord   (5개) : 기록 추가 및 max_records
    TestShadowEvaluatorSummary  (8개) : 성과 요약 계산
    TestShadowEvaluatorClear    (3개) : 초기화 및 recent()
    TestShadowRegistryRegister  (5개) : 등록/해제
    TestShadowRegistryRunAll    (7개) : 병렬 실행
    TestShadowRegistryRunOne    (4개) : 단건 실행
    TestShadowSummaryToDict     (3개) : ShadowSummary.to_dict()

총 55개 테스트
"""

import asyncio
import dataclasses
import pytest
import time

from domain.models.signal import Action, Signal
from application.analysis.shadow_mode import (
    ShadowEvaluator,
    ShadowRecord,
    ShadowRegistry,
    ShadowRunner,
    ShadowSummary,
)

# frozen dataclass FrozenInstanceError (Python 3.10 미만에서는 dataclasses.FrozenInstanceError 없음)
try:
    from dataclasses import FrozenInstanceError as dataclasses_FrozenInstanceError
except ImportError:
    dataclasses_FrozenInstanceError = Exception  # fallback


# ─── 테스트 헬퍼 ──────────────────────────────────────────────────

def _make_signal(
    ticker: str = "005930",
    action: Action = Action.BUY,
    score: float = 0.7,
    confidence: float = 0.8,
    price: float = 75000.0,
) -> Signal:
    return Signal(
        ticker=ticker,
        action=action,
        score=score,
        confidence=confidence,
        price=price,
    )


def _make_record(
    strategy_name: str = "test_strategy",
    ticker: str = "005930",
    production_action: Action = Action.BUY,
    shadow_action: Action = Action.BUY,
    agreement: bool = True,
    latency_ms: float = 12.5,
    error: str = None,
) -> ShadowRecord:
    return ShadowRecord(
        strategy_name=strategy_name,
        ticker=ticker,
        production_action=production_action,
        shadow_action=shadow_action,
        production_score=0.7,
        shadow_score=0.65,
        production_confidence=0.8,
        shadow_confidence=0.75,
        agreement=agreement,
        latency_ms=latency_ms,
        error=error,
    )


async def _ok_shadow(data: dict) -> Signal:
    """정상 동작하는 섀도우 함수."""
    return _make_signal(
        ticker=data.get("ticker", "005930"),
        action=Action.BUY,
        score=0.65,
        confidence=0.72,
        price=data.get("current_price", 75000.0),
    )


async def _sell_shadow(data: dict) -> Signal:
    """SELL을 반환하는 섀도우 함수."""
    return _make_signal(
        ticker=data.get("ticker", "005930"),
        action=Action.SELL,
        score=0.3,
        confidence=0.6,
        price=data.get("current_price", 75000.0),
    )


async def _error_shadow(data: dict) -> Signal:
    """예외를 발생시키는 섀도우 함수."""
    raise RuntimeError("Shadow pipeline failed")


async def _timeout_shadow(data: dict) -> Signal:
    """타임아웃을 유발하는 섀도우 함수."""
    await asyncio.sleep(10.0)
    return _make_signal()  # 도달 안 됨


# ═══════════════════════════════════════════════════════════════════
#  ShadowRecord (7개)
# ═══════════════════════════════════════════════════════════════════

class TestShadowRecord:
    def test_create_basic(self):
        r = _make_record()
        assert r.strategy_name == "test_strategy"
        assert r.ticker == "005930"
        assert r.agreement is True
        assert r.error is None

    def test_to_dict_has_required_keys(self):
        d = _make_record().to_dict()
        for key in (
            "strategy_name", "ticker",
            "production_action", "shadow_action",
            "production_score", "shadow_score",
            "production_confidence", "shadow_confidence",
            "agreement", "latency_ms", "error", "timestamp",
        ):
            assert key in d

    def test_to_dict_action_values_are_strings(self):
        d = _make_record().to_dict()
        assert isinstance(d["production_action"], str)
        assert isinstance(d["shadow_action"], str)

    def test_to_dict_scores_rounded(self):
        d = _make_record().to_dict()
        assert d["production_score"] == round(d["production_score"], 4)

    def test_disagreement_record(self):
        r = _make_record(
            production_action=Action.BUY,
            shadow_action=Action.SELL,
            agreement=False,
        )
        assert r.agreement is False
        assert r.to_dict()["agreement"] is False

    def test_error_record(self):
        r = _make_record(error="RuntimeError: something went wrong")
        assert r.error is not None
        assert "RuntimeError" in r.to_dict()["error"]

    def test_frozen_immutability(self):
        r = _make_record()
        # frozen=True dataclass는 속성 직접 수정이 불가
        with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
            r.ticker = "000000"  # type: ignore


# ═══════════════════════════════════════════════════════════════════
#  ShadowRunner 초기화 (4개)
# ═══════════════════════════════════════════════════════════════════

class TestShadowRunnerInit:
    def test_valid_init(self):
        runner = ShadowRunner("my_strategy", _ok_shadow)
        assert runner.strategy_name == "my_strategy"

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="strategy_name"):
            ShadowRunner("", _ok_shadow)

    def test_blank_name_raises(self):
        with pytest.raises(ValueError, match="strategy_name"):
            ShadowRunner("   ", _ok_shadow)

    def test_zero_timeout_raises(self):
        with pytest.raises(ValueError, match="timeout"):
            ShadowRunner("s", _ok_shadow, timeout=0.0)


# ═══════════════════════════════════════════════════════════════════
#  ShadowRunner.run() (9개)
# ═══════════════════════════════════════════════════════════════════

class TestShadowRunnerRun:
    @pytest.fixture
    def prod_signal(self):
        return _make_signal(action=Action.BUY)

    @pytest.fixture
    def data(self):
        return {"ticker": "005930", "current_price": 75000.0}

    @pytest.mark.asyncio
    async def test_run_success_agreement(self, data, prod_signal):
        runner = ShadowRunner("ok", _ok_shadow)
        record = await runner.run(data, prod_signal)
        assert record.error is None
        assert record.agreement is True  # 둘 다 BUY
        assert record.shadow_action == Action.BUY

    @pytest.mark.asyncio
    async def test_run_success_disagreement(self, data, prod_signal):
        runner = ShadowRunner("sell_s", _sell_shadow)
        record = await runner.run(data, prod_signal)
        assert record.agreement is False
        assert record.shadow_action == Action.SELL

    @pytest.mark.asyncio
    async def test_run_error_captured(self, data, prod_signal):
        runner = ShadowRunner("err_s", _error_shadow)
        record = await runner.run(data, prod_signal)
        assert record.error is not None
        assert "RuntimeError" in record.error
        assert record.shadow_action == Action.HOLD  # 에러 시 fallback

    @pytest.mark.asyncio
    async def test_run_timeout_captured(self, data, prod_signal):
        runner = ShadowRunner("to_s", _timeout_shadow, timeout=0.05)
        record = await runner.run(data, prod_signal)
        assert record.error is not None
        assert "timeout" in record.error.lower()
        assert record.shadow_action == Action.HOLD

    @pytest.mark.asyncio
    async def test_run_returns_shadow_record(self, data, prod_signal):
        runner = ShadowRunner("ok", _ok_shadow)
        record = await runner.run(data, prod_signal)
        assert isinstance(record, ShadowRecord)

    @pytest.mark.asyncio
    async def test_run_latency_positive(self, data, prod_signal):
        runner = ShadowRunner("ok", _ok_shadow)
        record = await runner.run(data, prod_signal)
        assert record.latency_ms >= 0.0

    @pytest.mark.asyncio
    async def test_run_strategy_name_propagated(self, data, prod_signal):
        runner = ShadowRunner("my_named_strategy", _ok_shadow)
        record = await runner.run(data, prod_signal)
        assert record.strategy_name == "my_named_strategy"

    @pytest.mark.asyncio
    async def test_run_ticker_from_production_signal(self, data, prod_signal):
        runner = ShadowRunner("ok", _ok_shadow)
        record = await runner.run(data, prod_signal)
        assert record.ticker == prod_signal.ticker

    @pytest.mark.asyncio
    async def test_run_error_shadow_confidence_zero(self, data, prod_signal):
        runner = ShadowRunner("err_s", _error_shadow)
        record = await runner.run(data, prod_signal)
        assert record.shadow_confidence == 0.0
        assert record.shadow_score == 0.0


# ═══════════════════════════════════════════════════════════════════
#  ShadowEvaluator.record() (5개)
# ═══════════════════════════════════════════════════════════════════

class TestShadowEvaluatorRecord:
    def test_add_record_increments_count(self):
        ev = ShadowEvaluator("s")
        ev.record(_make_record())
        assert ev.record_count == 1

    def test_add_multiple_records(self):
        ev = ShadowEvaluator("s")
        for _ in range(5):
            ev.record(_make_record())
        assert ev.record_count == 5

    def test_max_records_evicts_oldest(self):
        ev = ShadowEvaluator("s", max_records=3)
        for i in range(5):
            ev.record(_make_record(strategy_name=f"s{i}"))
        assert ev.record_count == 3

    def test_max_records_zero_raises(self):
        with pytest.raises(ValueError, match="max_records"):
            ShadowEvaluator("s", max_records=0)

    def test_eviction_removes_oldest(self):
        ev = ShadowEvaluator("s", max_records=2)
        r1 = _make_record(ticker="000001")
        r2 = _make_record(ticker="000002")
        r3 = _make_record(ticker="000003")
        ev.record(r1)
        ev.record(r2)
        ev.record(r3)
        # r1이 제거되고 r2, r3만 남아야 함
        recent = ev.recent(10)
        tickers = [r.ticker for r in recent]
        assert "000001" not in tickers
        assert "000002" in tickers
        assert "000003" in tickers


# ═══════════════════════════════════════════════════════════════════
#  ShadowEvaluator.summary() (8개)
# ═══════════════════════════════════════════════════════════════════

class TestShadowEvaluatorSummary:
    def test_empty_evaluator_summary(self):
        ev = ShadowEvaluator("s")
        s = ev.summary()
        assert s.total == 0
        assert s.agreement_rate == 0.0
        assert s.avg_shadow_confidence == 0.0

    def test_all_agree_rate_1(self):
        ev = ShadowEvaluator("s")
        for _ in range(4):
            ev.record(_make_record(agreement=True))
        s = ev.summary()
        assert s.agreement_rate == pytest.approx(1.0)

    def test_none_agree_rate_0(self):
        ev = ShadowEvaluator("s")
        for _ in range(3):
            ev.record(_make_record(agreement=False))
        s = ev.summary()
        assert s.agreement_rate == pytest.approx(0.0)

    def test_mixed_agreement_rate(self):
        ev = ShadowEvaluator("s")
        ev.record(_make_record(agreement=True))
        ev.record(_make_record(agreement=True))
        ev.record(_make_record(agreement=False))
        s = ev.summary()
        assert s.agreement_rate == pytest.approx(2 / 3)

    def test_error_count(self):
        ev = ShadowEvaluator("s")
        ev.record(_make_record(error=None))
        ev.record(_make_record(error="RuntimeError: x"))
        ev.record(_make_record(error="TimeoutError"))
        s = ev.summary()
        assert s.errors == 2

    def test_action_counts(self):
        ev = ShadowEvaluator("s")
        ev.record(_make_record(shadow_action=Action.BUY))
        ev.record(_make_record(shadow_action=Action.BUY))
        ev.record(_make_record(shadow_action=Action.SELL))
        s = ev.summary()
        # action_counts 키는 Action.value 형태 (대문자 또는 소문자)
        buy_key = Action.BUY.value
        sell_key = Action.SELL.value
        assert s.action_counts.get(buy_key, 0) == 2
        assert s.action_counts.get(sell_key, 0) == 1

    def test_avg_confidence_correct(self):
        ev = ShadowEvaluator("s")
        # shadow_confidence는 _make_record에서 0.75 고정
        for _ in range(4):
            ev.record(_make_record())
        s = ev.summary()
        assert s.avg_shadow_confidence == pytest.approx(0.75)

    def test_total_count(self):
        ev = ShadowEvaluator("s")
        for _ in range(7):
            ev.record(_make_record())
        s = ev.summary()
        assert s.total == 7


# ═══════════════════════════════════════════════════════════════════
#  ShadowEvaluator clear/recent (3개)
# ═══════════════════════════════════════════════════════════════════

class TestShadowEvaluatorClear:
    def test_clear_resets_count(self):
        ev = ShadowEvaluator("s")
        for _ in range(5):
            ev.record(_make_record())
        ev.clear()
        assert ev.record_count == 0

    def test_recent_returns_last_n(self):
        ev = ShadowEvaluator("s")
        for i in range(10):
            ev.record(_make_record(latency_ms=float(i)))
        recent = ev.recent(3)
        assert len(recent) == 3
        # 가장 최근 3개: latency 7, 8, 9
        latencies = [r.latency_ms for r in recent]
        assert 9.0 in latencies

    def test_recent_all_when_n_exceeds_count(self):
        ev = ShadowEvaluator("s")
        for _ in range(3):
            ev.record(_make_record())
        assert len(ev.recent(100)) == 3


# ═══════════════════════════════════════════════════════════════════
#  ShadowRegistry 등록/해제 (5개)
# ═══════════════════════════════════════════════════════════════════

class TestShadowRegistryRegister:
    def test_register_adds_strategy(self):
        reg = ShadowRegistry()
        reg.register("s1", _ok_shadow)
        assert "s1" in reg

    def test_len_reflects_count(self):
        reg = ShadowRegistry()
        reg.register("s1", _ok_shadow)
        reg.register("s2", _sell_shadow)
        assert len(reg) == 2

    def test_registered_names(self):
        reg = ShadowRegistry()
        reg.register("alpha", _ok_shadow)
        reg.register("beta", _sell_shadow)
        assert set(reg.registered_names) == {"alpha", "beta"}

    def test_unregister_returns_true(self):
        reg = ShadowRegistry()
        reg.register("s1", _ok_shadow)
        assert reg.unregister("s1") is True
        assert "s1" not in reg

    def test_unregister_nonexistent_returns_false(self):
        reg = ShadowRegistry()
        assert reg.unregister("nonexistent") is False


# ═══════════════════════════════════════════════════════════════════
#  ShadowRegistry.run_all() (7개)
# ═══════════════════════════════════════════════════════════════════

class TestShadowRegistryRunAll:
    @pytest.fixture
    def prod_signal(self):
        return _make_signal(action=Action.BUY)

    @pytest.fixture
    def data(self):
        return {"ticker": "005930", "current_price": 75000.0}

    @pytest.mark.asyncio
    async def test_run_all_empty_returns_empty_list(self, data, prod_signal):
        reg = ShadowRegistry()
        result = await reg.run_all(data, prod_signal)
        assert result == []

    @pytest.mark.asyncio
    async def test_run_all_returns_all_records(self, data, prod_signal):
        reg = ShadowRegistry()
        reg.register("s1", _ok_shadow)
        reg.register("s2", _sell_shadow)
        records = await reg.run_all(data, prod_signal)
        assert len(records) == 2

    @pytest.mark.asyncio
    async def test_run_all_records_stored_in_evaluator(self, data, prod_signal):
        reg = ShadowRegistry()
        reg.register("s1", _ok_shadow)
        await reg.run_all(data, prod_signal)
        summary = reg.summary("s1")
        assert summary.total == 1

    @pytest.mark.asyncio
    async def test_run_all_error_strategy_captured(self, data, prod_signal):
        reg = ShadowRegistry()
        reg.register("err", _error_shadow)
        records = await reg.run_all(data, prod_signal)
        assert records[0].error is not None

    @pytest.mark.asyncio
    async def test_run_all_multiple_calls_accumulate(self, data, prod_signal):
        reg = ShadowRegistry()
        reg.register("s1", _ok_shadow)
        await reg.run_all(data, prod_signal)
        await reg.run_all(data, prod_signal)
        assert reg.summary("s1").total == 2

    @pytest.mark.asyncio
    async def test_all_summaries_returns_all(self, data, prod_signal):
        reg = ShadowRegistry()
        reg.register("a", _ok_shadow)
        reg.register("b", _sell_shadow)
        await reg.run_all(data, prod_signal)
        summaries = reg.all_summaries()
        assert set(summaries.keys()) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_run_all_nonblocking_for_errors(self, data, prod_signal):
        """에러 전략이 있어도 나머지 전략 결과가 정상 반환된다."""
        reg = ShadowRegistry()
        reg.register("good", _ok_shadow)
        reg.register("bad", _error_shadow)
        records = await reg.run_all(data, prod_signal)
        names = {r.strategy_name for r in records}
        assert "good" in names
        assert "bad" in names


# ═══════════════════════════════════════════════════════════════════
#  ShadowRegistry.run_one() (4개)
# ═══════════════════════════════════════════════════════════════════

class TestShadowRegistryRunOne:
    @pytest.fixture
    def prod_signal(self):
        return _make_signal(action=Action.BUY)

    @pytest.fixture
    def data(self):
        return {"ticker": "005930", "current_price": 75000.0}

    @pytest.mark.asyncio
    async def test_run_one_known_strategy(self, data, prod_signal):
        reg = ShadowRegistry()
        reg.register("s1", _ok_shadow)
        record = await reg.run_one("s1", data, prod_signal)
        assert record is not None
        assert record.strategy_name == "s1"

    @pytest.mark.asyncio
    async def test_run_one_unknown_strategy_returns_none(self, data, prod_signal):
        reg = ShadowRegistry()
        result = await reg.run_one("nonexistent", data, prod_signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_run_one_records_in_evaluator(self, data, prod_signal):
        reg = ShadowRegistry()
        reg.register("s1", _ok_shadow)
        await reg.run_one("s1", data, prod_signal)
        assert reg.summary("s1").total == 1

    @pytest.mark.asyncio
    async def test_run_one_summary_returns_none_for_unknown(self, data, prod_signal):
        reg = ShadowRegistry()
        assert reg.summary("unknown") is None


# ═══════════════════════════════════════════════════════════════════
#  ShadowSummary.to_dict() (3개)
# ═══════════════════════════════════════════════════════════════════

class TestShadowSummaryToDict:
    def _make_summary(self):
        ev = ShadowEvaluator("test_s")
        ev.record(_make_record(agreement=True))
        ev.record(_make_record(agreement=False, error="err"))
        return ev.summary()

    def test_to_dict_has_required_keys(self):
        d = self._make_summary().to_dict()
        for key in (
            "strategy_name", "total", "agreements", "errors",
            "agreement_rate", "avg_shadow_confidence",
            "avg_latency_ms", "action_counts",
        ):
            assert key in d

    def test_to_dict_values_correct(self):
        d = self._make_summary().to_dict()
        assert d["total"] == 2
        assert d["agreements"] == 1
        assert d["errors"] == 1

    def test_to_dict_agreement_rate_rounded(self):
        d = self._make_summary().to_dict()
        rate = d["agreement_rate"]
        assert rate == round(rate, 4)
