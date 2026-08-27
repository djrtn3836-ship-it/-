"""
tests/unit/test_pipeline_manager.py - v1.1 (Session 12, 자체 검증 보강본)
PipelineManager v2.0 단위 테스트 (35개)
"""

import asyncio
import pytest
from dataclasses import FrozenInstanceError

from orchestrator.pipeline_manager import PipelineManager, PipelineResult, StepResult


async def ok_step():
    return "ok"


async def fail_step():
    raise ValueError("step failed")


async def slow_step():
    await asyncio.sleep(0.01)
    return "slow_ok"


class TestStepResult:
    def test_creation(self):
        r = StepResult("step1", True, 12.5, "output")
        assert r.step_name == "step1"
        assert r.success is True

    def test_to_dict_keys(self):
        d = StepResult("step1", True, 12.5, "output").to_dict()
        for k in ["step_name", "success", "latency_ms", "error", "retries"]:
            assert k in d

    def test_frozen(self):
        r = StepResult("step1", True, 12.5, "output")
        with pytest.raises(FrozenInstanceError):
            r.success = False

    def test_error_none_by_default(self):
        assert StepResult("step1", True, 1.0, None).error is None


class TestPipelineResult:
    def test_initial_success_true(self):
        assert PipelineResult("test_pipeline").success is True

    def test_add_success_step(self):
        pr = PipelineResult("p")
        pr.add_step(StepResult("s1", True, 10.0, "ok"))
        assert pr.success is True
        assert len(pr.steps) == 1

    def test_add_failed_step_marks_failure(self):
        pr = PipelineResult("p")
        pr.add_step(StepResult("s1", False, 5.0, None, error="err"))
        assert pr.success is False

    def test_total_latency_accumulated(self):
        pr = PipelineResult("p")
        pr.add_step(StepResult("s1", True, 10.0, "ok"))
        pr.add_step(StepResult("s2", True, 5.0, "ok"))
        assert pr.total_latency_ms == pytest.approx(15.0)

    def test_slowest_step(self):
        pr = PipelineResult("p")
        pr.add_step(StepResult("fast", True, 2.0, "ok"))
        pr.add_step(StepResult("slow", True, 20.0, "ok"))
        assert pr.slowest_step.step_name == "slow"

    def test_slowest_step_empty(self):
        assert PipelineResult("p").slowest_step is None

    def test_to_dict_keys(self):
        pr = PipelineResult("p")
        pr.add_step(StepResult("s1", True, 1.0, "ok"))
        d = pr.to_dict()
        for k in ["pipeline_name", "steps", "total_latency_ms", "success", "failed_steps"]:
            assert k in d

    def test_failed_steps_listed(self):
        pr = PipelineResult("p")
        pr.add_step(StepResult("ok_step", True, 1.0, "ok"))
        pr.add_step(StepResult("bad_step", False, 1.0, None, error="err"))
        assert "bad_step" in pr.to_dict()["failed_steps"]


class TestPipelineManagerRun:
    def test_all_success(self):
        pm = PipelineManager(max_retries=0)
        result = asyncio.run(pm.run("test", [("s1", ok_step), ("s2", ok_step)]))
        assert result.success is True
        assert len(result.steps) == 2

    def test_first_step_fails_stops_pipeline(self):
        pm = PipelineManager(max_retries=0)
        result = asyncio.run(pm.run("test", [("fail", fail_step), ("should_not_run", ok_step)]))
        assert result.success is False
        assert len(result.steps) == 1

    def test_empty_steps(self):
        pm = PipelineManager()
        result = asyncio.run(pm.run("empty", []))
        assert result.success is True
        assert len(result.steps) == 0

    def test_latency_measured(self):
        pm = PipelineManager(max_retries=0)
        result = asyncio.run(pm.run("test", [("slow", slow_step)]))
        assert result.steps[0].latency_ms >= 0.0

    def test_result_stored_in_history(self):
        pm = PipelineManager(max_retries=0)
        asyncio.run(pm.run("test", [("s1", ok_step)]))
        assert len(pm.recent_results()) == 1


class TestPipelineManagerStepOrder:
    def test_steps_run_in_order(self):
        order = []

        async def step_a():
            order.append("a")

        async def step_b():
            order.append("b")

        pm = PipelineManager(max_retries=0)
        asyncio.run(pm.run("test", [("a", step_a), ("b", step_b)]))
        assert order == ["a", "b"]

    def test_partial_success_before_failure(self):
        pm = PipelineManager(max_retries=0)
        result = asyncio.run(
            pm.run("test", [("ok1", ok_step), ("fail", fail_step), ("ok2", ok_step)])
        )
        assert len(result.steps) == 2
        assert result.steps[0].success is True
        assert result.steps[1].success is False

    def test_step_output_captured(self):
        async def returns_value():
            return {"key": "value"}

        pm = PipelineManager(max_retries=0)
        result = asyncio.run(pm.run("test", [("s", returns_value)]))
        assert result.steps[0].output == {"key": "value"}


class TestPipelineManagerRetry:
    def test_retry_on_failure(self):
        call_count = {"n": 0}

        async def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError("flaky")
            return "ok"

        pm = PipelineManager(max_retries=3, backoff_base=0.001)
        result = asyncio.run(pm.run("test", [("flaky", flaky)]))
        assert result.success is True
        assert result.steps[0].retries == 2

    def test_exhausted_retries_marks_failure(self):
        pm = PipelineManager(max_retries=2, backoff_base=0.001)
        result = asyncio.run(pm.run("test", [("always_fail", fail_step)]))
        assert result.success is False
        assert result.steps[0].retries == 2

    def test_no_retry_on_success(self):
        pm = PipelineManager(max_retries=3)
        result = asyncio.run(pm.run("test", [("ok", ok_step)]))
        assert result.steps[0].retries == 0


class TestPipelineManagerHealth:
    def test_no_runs_health(self):
        h = PipelineManager().get_health()
        assert h["status"] == "no_runs"
        assert h["success_rate"] == 1.0

    def test_all_success_healthy(self):
        pm = PipelineManager(max_retries=0)
        for _ in range(5):
            asyncio.run(pm.run("t", [("s", ok_step)]))
        h = pm.get_health()
        assert h["status"] == "healthy"
        assert h["success_rate"] == pytest.approx(1.0)

    def test_all_fail_degraded(self):
        pm = PipelineManager(max_retries=0)
        for _ in range(5):
            asyncio.run(pm.run("t", [("f", fail_step)]))
        assert pm.get_health()["status"] == "degraded"

    def test_recent_results_limit(self):
        pm = PipelineManager(max_retries=0)
        for _ in range(15):
            asyncio.run(pm.run("t", [("s", ok_step)]))
        assert len(pm.recent_results(5)) == 5

    def test_total_runs_counted(self):
        pm = PipelineManager(max_retries=0)
        for _ in range(3):
            asyncio.run(pm.run("t", [("s", ok_step)]))
        assert pm.get_health()["total_runs"] == 3


class TestPipelineManagerHealthBoundary:
    def test_health_exactly_80_percent(self):
        pm = PipelineManager(max_retries=0)
        for _ in range(4):
            asyncio.run(pm.run("t", [("s", ok_step)]))
        asyncio.run(pm.run("t", [("f", fail_step)]))
        h = pm.get_health()
        assert h["success_rate"] == pytest.approx(0.8)
        assert h["status"] == "healthy"

    def test_recent_failures_capped_at_5(self):
        pm = PipelineManager(max_retries=0)
        for i in range(10):
            asyncio.run(pm.run(f"pipeline_{i}", [("f", fail_step)]))
        assert len(pm.get_health()["recent_failures"]) <= 5

    def test_history_uses_only_last_20_for_health(self):
        pm = PipelineManager(max_retries=0)
        for _ in range(15):
            asyncio.run(pm.run("t", [("s", ok_step)]))
        for _ in range(5):
            asyncio.run(pm.run("t", [("f", fail_step)]))
        assert pm.get_health()["success_rate"] == pytest.approx(0.75)


class TestPipelineManagerLifecycle:
    def test_start_sets_running_true(self):
        pm = PipelineManager()
        asyncio.run(pm.start())
        assert pm._running is True

    def test_stop_sets_running_false(self):
        pm = PipelineManager()
        asyncio.run(pm.start())
        asyncio.run(pm.stop())
        assert pm._running is False

    def test_no_arg_constructor_backward_compatible(self):
        # main.py 레거시 호출 패턴: PipelineManager() 무인자 호환성 확인
        assert PipelineManager() is not None


class TestPipelineResultEdgeCases:
    def test_recent_results_default_n(self):
        pm = PipelineManager(max_retries=0)
        for _ in range(3):
            asyncio.run(pm.run("t", [("s", ok_step)]))
        assert len(pm.recent_results()) == 3
