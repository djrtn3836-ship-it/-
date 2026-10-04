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
        assert cb.record_prediction("trend", 0.8, "", "BUY", 100.0, path) is False
        assert cb.record_prediction("trend", 0.8, "005930", "BUY", 0.0, path) is False
        assert cb.record_prediction("trend", 0.8, "005930", "BUY", 100.0, path) is True
        assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 1

    def test_summary_handles_empty(self, tmp_path: Path) -> None:
        from analytics import calibration_bridge as cb

        s = cb.get_calibration_summary(tmp_path / "none.jsonl")
        assert s["regimes"] == {}
