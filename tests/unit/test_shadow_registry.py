# -*- coding: utf-8 -*-
"""tests/unit/test_shadow_registry.py - 섀도우 전략 레지스트리 검증 (P8-2)."""

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from application.analysis.shadow_registry import (
    ShadowRegistry,
    low_volatility_shadow,
)
from domain.models.signal import Action, Signal


def _signal(action: Action = Action.BUY, ticker: str = "005930", price: float = 100.0) -> Signal:
    return Signal(ticker=ticker, action=action, score=0.7, confidence=0.6, price=price)


def _data(closes: List[float], ticker: str = "005930") -> Dict[str, Any]:
    return {"ticker": ticker, "price": closes[-1], "tech_data": {"closes": closes}}


def _rising(n: int = 80) -> List[float]:
    return [100.0 * (1.0005 ** i) for i in range(n)]


def _choppy(n: int = 80) -> List[float]:
    return [100.0 * (1 + (0.05 if i % 2 else -0.05)) for i in range(n)]


class TestLowVolStrategy:
    async def test_low_vol_uptrend_is_buy(self) -> None:
        closes = _rising()
        sig = await low_volatility_shadow(_data(closes))

        assert sig.action is Action.BUY
        assert sig.ticker == "005930"

    async def test_high_vol_is_hold(self) -> None:
        sig = await low_volatility_shadow(_data(_choppy()))
        assert sig.action is Action.HOLD

    async def test_insufficient_data_is_hold(self) -> None:
        sig = await low_volatility_shadow(_data([100.0, 101.0, 102.0]))
        assert sig.action is Action.HOLD

    async def test_is_coroutine(self) -> None:
        """계약: shadow_fn은 async callable이어야 한다(await 가능)."""
        coro = low_volatility_shadow(_data(_rising()))
        assert hasattr(coro, "__await__")
        await coro


class TestRegistry:
    def test_register_and_names(self, tmp_path: Path) -> None:
        reg = ShadowRegistry(records_path=tmp_path / "r.jsonl")
        reg.register_defaults()

        assert reg.strategy_names == ["low_vol_120d"]

    def test_duplicate_registration_ignored(self, tmp_path: Path) -> None:
        reg = ShadowRegistry(records_path=tmp_path / "r.jsonl")
        reg.register("a", low_volatility_shadow)
        reg.register("a", low_volatility_shadow)

        assert reg.strategy_names == ["a"]

    async def test_evaluate_records_and_writes_jsonl(self, tmp_path: Path) -> None:
        out = tmp_path / "r.jsonl"
        reg = ShadowRegistry(records_path=out)
        reg.register_defaults()

        records = await reg.evaluate(_data(_rising()), _signal())

        assert len(records) == 1
        assert records[0].strategy_name == "low_vol_120d"
        assert records[0].error is None
        assert out.exists() and len(out.read_text(encoding="utf-8").strip().splitlines()) == 1
        row = json.loads(out.read_text(encoding="utf-8").strip())
        assert row["shadow_action"] == "BUY"

    async def test_broken_strategy_is_contained(self, tmp_path: Path) -> None:
        async def _boom(data: Any) -> Any:
            raise RuntimeError("전략 폭발")

        reg = ShadowRegistry(records_path=tmp_path / "r.jsonl")
        reg.register("broken", _boom)

        records = await reg.evaluate(_data(_rising()), _signal())

        assert len(records) == 1
        assert records[0].error and "전략 폭발" in records[0].error
        assert reg.summary()["stats"]["errors"] == 1

    async def test_non_async_strategy_is_contained(self, tmp_path: Path) -> None:
        """async 계약 위반(동기 함수)도 예외를 새지 않고 error로 기록한다."""
        def _sync(data: Any) -> Any:
            return _signal()

        reg = ShadowRegistry(records_path=tmp_path / "r.jsonl")
        reg.register("sync", _sync)

        records = await reg.evaluate(_data(_rising()), _signal())
        assert records and records[0].error is not None

    async def test_disabled_by_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHADOW_MODE_ENABLED", "false")
        reg = ShadowRegistry(records_path=tmp_path / "r.jsonl")
        reg.register_defaults()

        records = await reg.evaluate(_data(_rising()), _signal())

        assert records == []
        assert reg.summary()["stats"]["skipped"] == 1

    async def test_no_strategies_is_skipped(self, tmp_path: Path) -> None:
        reg = ShadowRegistry(records_path=tmp_path / "r.jsonl")
        assert await reg.evaluate(_data(_rising()), _signal()) == []

    async def test_summary_shape(self, tmp_path: Path) -> None:
        reg = ShadowRegistry(records_path=tmp_path / "r.jsonl")
        reg.register_defaults()
        await reg.evaluate(_data(_rising()), _signal())

        s = reg.summary()

        assert set(s) == {"stats", "strategies", "recent"}
        assert s["strategies"]["low_vol_120d"]["total"] == 1
        assert s["stats"]["recorded"] == 1

    async def test_recent_returns_dicts(self, tmp_path: Path) -> None:
        reg = ShadowRegistry(records_path=tmp_path / "r.jsonl")
        reg.register_defaults()
        await reg.evaluate(_data(_rising()), _signal())

        recent = reg.recent(5)
        assert recent and isinstance(recent[0], dict)
        assert "agreement" in recent[0]

    async def test_jsonl_append_failure_is_contained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """기록 파일 저장이 실패해도 평가 자체는 성공해야 한다."""
        reg = ShadowRegistry(records_path=tmp_path / "no" / "such" / "r.jsonl")
        reg.register_defaults()
        monkeypatch.setattr(
            "pathlib.Path.mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("권한 없음"))
        )

        records = await reg.evaluate(_data(_rising()), _signal())

        assert len(records) == 1
        assert records[0].error is None


class TestPipelineHook:
    async def test_pipeline_process_invokes_shadow(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """SignalPipeline.process()가 섀도우 평가를 호출해야 한다(P8-2 배선)."""
        from application.analysis import signal_pipeline as sp

        called: List[str] = []

        class _Reg:
            async def evaluate(self, data: Dict[str, Any], signal: Signal) -> List[Any]:
                called.append(str(data.get("ticker")))
                return []

        monkeypatch.setattr(
            "application.analysis.shadow_registry.get_shadow_registry", lambda: _Reg()
        )

        class _Pipe:
            async def _run_shadow(self, data: Dict[str, Any], signal: Signal) -> None:
                await _Reg().evaluate(data, signal)

        await _Pipe()._run_shadow({"ticker": "005930"}, _signal())
        assert called == ["005930"]

        # process()가 훅을 await하는지 소스로 확인
        # (process는 tracer 데코레이터로 감싸져 있어 __code__ 검사가 불가)
        assert hasattr(sp.SignalPipeline, "_run_shadow")
        src = (Path(sp.__file__)).read_text(encoding="utf-8")
        assert "await self._run_shadow(data, signal)" in src


class TestCalibrationBridge:
    """P8-3: 가격 기반 채점으로 결과 라벨을 만들어 캘리브레이션 폐루프를 닫는다."""

    async def test_judge_rules(self) -> None:
        from analytics.calibration_bridge import judge

        assert judge("BUY", 100.0, 101.0) is True
        assert judge("BUY", 100.0, 99.0) is False
        assert judge("SELL", 100.0, 99.0) is True
        assert judge("SELL", 100.0, 101.0) is False
        assert judge("HOLD", 100.0, 100.2) is True
        assert judge("HOLD", 100.0, 110.0) is False
        assert judge("HOLD", 0.0, 100.0) is None

    async def test_settle_writes_and_is_idempotent(self, tmp_path: Path) -> None:
        from analytics import calibration_bridge as cb
        from datetime import datetime, timedelta

        past = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        pred_path = tmp_path / "p.jsonl"
        settled_path = tmp_path / "s.jsonl"
        pred_path.write_text(
            json.dumps({"date": past, "regime": "trend", "confidence": 0.8,
                        "ticker": "005930", "action": "BUY", "price": 100.0}) + "\n",
            encoding="utf-8",
        )

        class _DB:
            async def get_ohlcv_range(self, ticker: str, start: str, end: str):
                return [{"date": past, "close": 100.0}, {"date": end, "close": 105.0}]

        r1 = await cb.settle_predictions(_DB(), pred_path=pred_path, settled_path=settled_path)
        r2 = await cb.settle_predictions(_DB(), pred_path=pred_path, settled_path=settled_path)

        assert r1["new"] == 1 and r1["status"] == "ok"
        assert r2["already"] == 1 and (r2["new"] == 0)
        rows = [json.loads(l) for l in settled_path.read_text(encoding="utf-8").splitlines()]
        assert rows[0]["actual_win"] is True and rows[0]["return"] > 0

    async def test_tracker_rebuilt_from_settled(self, tmp_path: Path) -> None:
        from analytics import calibration_bridge as cb

        settled_path = tmp_path / "s.jsonl"
        with open(settled_path, "w", encoding="utf-8") as f:
            for i in range(15):
                f.write(json.dumps({"regime": "trend", "confidence": 0.85,
                                    "actual_win": bool(i % 5)}) + "\n")

        tracker = cb.build_tracker(settled_path)
        cal = tracker.get_calibration("trend")

        assert cal["status"] != "insufficient_data"
        assert cal["total_samples"] == 15
        assert "ece" in cal

    def test_record_prediction_skips_invalid(self, tmp_path: Path) -> None:
        from analytics import calibration_bridge as cb

        path = tmp_path / "p.jsonl"
        assert cb.record_prediction("trend", 0.8, "", "BUY", 100.0, path=path) is False
        assert cb.record_prediction("trend", 0.8, "005930", "BUY", 0.0, path=path) is False
        assert cb.record_prediction("trend", 0.8, "005930", "BUY", 100.0, path=path) is True
        assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 1

    def test_summary_handles_empty(self, tmp_path: Path) -> None:
        from analytics import calibration_bridge as cb

        s = cb.get_calibration_summary(tmp_path / "none.jsonl")
        assert s["regimes"] == {}


class TestTraceBridge:
    """P8-4: 의사결정 경로 트리 브리지."""

    def setup_method(self) -> None:
        from observability import trace_bridge as tb

        tb.clear()

    def test_record_and_render(self) -> None:
        from observability.trace_bridge import get_trace_text, record_stage

        nid = record_stage("T-100", "SignalPipeline", "process", "in", "out", 5.0, True)
        text = get_trace_text("T-100")

        assert nid
        assert "SignalPipeline" in text and "process" in text

    def test_unknown_trace_is_friendly(self) -> None:
        from observability.trace_bridge import get_trace_text

        assert "찾을 수 없습니다" in get_trace_text("NOPE")

    def test_empty_trace_id_ignored(self) -> None:
        from observability.trace_bridge import record_stage

        assert record_stage("", "M", "op") is None

    def test_latest_and_recent(self) -> None:
        from observability.trace_bridge import latest_trace_id, recent_summaries, record_stage

        record_stage("T-A", "M", "op1")
        record_stage("T-B", "M", "op2")

        assert latest_trace_id() == "T-B"
        assert len(recent_summaries(5)) == 2

    def test_failure_does_not_raise(self, monkeypatch: Any) -> None:
        from observability import trace_bridge as tb

        monkeypatch.setattr(tb, "get_trace_tree", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert tb.record_stage("T-X", "M", "op") is None
        assert "실패" in tb.get_trace_text("T-X")

    def test_pipeline_records_trace(self) -> None:
        """SignalPipeline에 trace 기록 훅이 있는지(소스 계약)."""
        from pathlib import Path

        from application.analysis import signal_pipeline as sp

        assert hasattr(sp.SignalPipeline, "_record_trace")
        src = Path(sp.__file__).read_text(encoding="utf-8")
        assert "self._record_trace(data, signal, _t0)" in src
        assert "await self._run_shadow(data, signal)" in src


class TestNonPriceFactors:
    """P12-3: 비가격 팩터(감성/공시) + 조건 태그."""

    async def test_compute_without_sources_is_safe(self) -> None:
        from application.analysis.nonprice_factors import compute

        f = await compute("005930")

        assert f.ticker == "005930"
        assert f.sentiment_score is None
        assert "disc_no" in f.tags
        assert f.error is None

    async def test_positive_sentiment_tags_and_boost(self) -> None:
        from application.analysis.nonprice_factors import compute

        class _Sent:
            async def get_sentiment(self, ticker: str):
                class R:
                    score = 0.6
                    impact_score = 0.8
                    news_count = 7
                    class L:
                        value = "positive"
                    label = L()
                return R()

        f = await compute("005930", sentiment_pipeline=_Sent())

        assert f.sentiment_score == pytest.approx(0.6)
        assert "sent_pos" in f.tags
        assert "news_busy" in f.tags
        assert f.boost > 0

    async def test_negative_sentiment_is_negative_boost(self) -> None:
        from application.analysis.nonprice_factors import compute

        class _Sent:
            async def get_sentiment(self, ticker: str):
                class R:
                    score = -0.7
                    impact_score = 0.9
                    news_count = 2
                    class L:
                        value = "negative"
                    label = L()
                return R()

        f = await compute("005930", sentiment_pipeline=_Sent())

        assert "sent_neg" in f.tags
        assert f.boost < 0

    async def test_sentiment_failure_is_contained(self) -> None:
        from application.analysis.nonprice_factors import compute

        class _Broken:
            async def get_sentiment(self, ticker: str):
                raise RuntimeError("뉴스 크롤 실패")

        f = await compute("005930", sentiment_pipeline=_Broken())

        assert f.sentiment_score is None      # 추정하지 않는다
        assert f.error is None

    async def test_disabled_by_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from application.analysis import nonprice_factors as npf

        monkeypatch.setenv("NONPRICE_FACTORS_ENABLED", "false")
        f = await npf.compute("005930")

        assert f.tags == [] and f.error == "disabled"

    def test_dart_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from application.analysis.nonprice_factors import dart_enabled

        monkeypatch.delenv("NONPRICE_DART_ENABLED", raising=False)
        assert dart_enabled() is False

    def test_boost_is_clamped(self) -> None:
        from application.analysis.nonprice_factors import MAX_BOOST, apply_boost

        assert apply_boost(0.99, 0.5) == 1.0
        assert apply_boost(0.01, -0.5) == 0.0
        assert apply_boost(0.5, MAX_BOOST) == pytest.approx(0.55, abs=1e-9)


class TestTagWinRates:
    """P12-4: 조건 태그별 승률 (표본 부족은 제외)."""

    def _write(self, tmp_path: Path, rows: list) -> Path:
        p = tmp_path / "settled.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return p

    def test_win_rate_per_tag(self, tmp_path: Path) -> None:
        from analytics.calibration_bridge import get_tag_win_rates

        p = self._write(tmp_path, [
            {"tags": ["sent_pos"], "actual_win": True},
            {"tags": ["sent_pos"], "actual_win": True},
            {"tags": ["sent_pos"], "actual_win": False},
            {"tags": ["sent_neg"], "actual_win": False},
            {"tags": ["sent_neg"], "actual_win": False},
            {"tags": ["sent_neg"], "actual_win": True},
        ])

        r = get_tag_win_rates(min_samples=3, settled_path=p)

        assert r["tags"]["sent_pos"]["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
        assert r["tags"]["sent_neg"]["win_rate"] == pytest.approx(1 / 3, abs=1e-4)
        assert r["tagged"] == 6

    def test_small_sample_excluded(self, tmp_path: Path) -> None:
        from analytics.calibration_bridge import get_tag_win_rates

        p = self._write(tmp_path, [{"tags": ["rare"], "actual_win": True}])
        r = get_tag_win_rates(min_samples=5, settled_path=p)

        assert r["tags"]["rare"]["status"] == "insufficient_data"
        assert r["tags"]["rare"]["win_rate"] is None

    def test_records_without_tags_ignored(self, tmp_path: Path) -> None:
        from analytics.calibration_bridge import get_tag_win_rates

        p = self._write(tmp_path, [{"actual_win": True}, {"actual_win": False}])
        r = get_tag_win_rates(min_samples=1, settled_path=p)

        assert r["tags"] == {} and r["tagged"] == 0 and r["total_settled"] == 2

    def test_empty_file(self, tmp_path: Path) -> None:
        from analytics.calibration_bridge import get_tag_win_rates

        assert get_tag_win_rates(settled_path=tmp_path / "none.jsonl")["tags"] == {}


class TestPredictionTagsRecorded:
    """P12-3: 태그가 예측 기록에 저장되는지."""

    def test_record_prediction_with_tags(self, tmp_path: Path) -> None:
        from analytics.calibration_bridge import record_prediction

        p = tmp_path / "p.jsonl"
        ok = record_prediction("Bull", 0.8, "005930", "BUY", 70000.0,
                               tags=["sent_pos", "disc_no"],
                               factors={"sentiment_score": 0.5}, path=p)

        assert ok
        row = json.loads(p.read_text(encoding="utf-8").strip())
        assert row["tags"] == ["sent_pos", "disc_no"]
        assert row["factors"]["sentiment_score"] == 0.5

    def test_record_without_tags_has_no_field(self, tmp_path: Path) -> None:
        from analytics.calibration_bridge import record_prediction

        p = tmp_path / "p.jsonl"
        record_prediction("Bull", 0.8, "005930", "BUY", 70000.0, path=p)

        assert "tags" not in json.loads(p.read_text(encoding="utf-8").strip())
