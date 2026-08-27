"""
orchestrator/pipeline_manager.py - v2.0 (Session 12)

Pipeline Manager: 단계별 지연 추적 + 실패 재시도 + HealthCheck 통합.
main.py의 레거시 호출 패턴(`PipelineManager()` 무인자 + `await start()/stop()`)과
완전히 호환됩니다.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("pipeline_manager")

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_BASE = 2.0


@dataclass(frozen=True)
class StepResult:
    """파이프라인 단계 실행 결과"""
    step_name: str
    success: bool
    latency_ms: float
    output: Any
    error: Optional[str] = None
    retries: int = 0

    def to_dict(self) -> dict:
        return {
            "step_name": self.step_name,
            "success": self.success,
            "latency_ms": round(self.latency_ms, 3),
            "error": self.error,
            "retries": self.retries,
        }


@dataclass
class PipelineResult:
    """파이프라인 전체 실행 결과"""
    pipeline_name: str
    steps: List[StepResult] = field(default_factory=list)
    total_latency_ms: float = 0.0
    success: bool = True

    def add_step(self, result: StepResult) -> None:
        self.steps.append(result)
        self.total_latency_ms += result.latency_ms
        if not result.success:
            self.success = False

    def to_dict(self) -> dict:
        return {
            "pipeline_name": self.pipeline_name,
            "steps": [s.to_dict() for s in self.steps],
            "total_latency_ms": round(self.total_latency_ms, 3),
            "success": self.success,
            "step_count": len(self.steps),
            "failed_steps": [s.step_name for s in self.steps if not s.success],
        }

    @property
    def slowest_step(self) -> Optional[StepResult]:
        if not self.steps:
            return None
        return max(self.steps, key=lambda s: s.latency_ms)


class PipelineManager:
    """단계별 지연 추적 + 실패 재시도 파이프라인 실행기."""

    def __init__(
        self,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base: float = _DEFAULT_BACKOFF_BASE,
    ) -> None:
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._history: List[PipelineResult] = []
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("PipelineManager started")

    async def stop(self) -> None:
        self._running = False
        logger.info("PipelineManager stopped")

    async def run(self, pipeline_name: str, steps: List[tuple]) -> PipelineResult:
        result = PipelineResult(pipeline_name=pipeline_name)

        for step_name, step_fn in steps:
            step_result = await self._run_step_with_retry(step_name, step_fn)
            result.add_step(step_result)
            if not step_result.success:
                logger.warning(f"[{pipeline_name}] 단계 '{step_name}' 실패 → 파이프라인 중단")
                break

        self._history.append(result)
        if len(self._history) > 200:
            self._history = self._history[-200:]
        return result

    async def _run_step_with_retry(
        self,
        step_name: str,
        step_fn: Callable[[], Coroutine],
        max_retries: Optional[int] = None,
    ) -> StepResult:
        retries_limit = max_retries if max_retries is not None else self._max_retries
        last_error: Optional[str] = None
        attempt = 0

        while attempt <= retries_limit:
            t_start = time.perf_counter()
            try:
                output = await step_fn()
                latency_ms = (time.perf_counter() - t_start) * 1000
                return StepResult(step_name, True, latency_ms, output, retries=attempt)
            except Exception as e:
                latency_ms = (time.perf_counter() - t_start) * 1000
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    f"[{step_name}] 시도 {attempt + 1}/{retries_limit + 1} 실패: {last_error}"
                )
                attempt += 1
                if attempt <= retries_limit:
                    backoff = self._backoff_base ** (attempt - 1)
                    await asyncio.sleep(backoff)

        return StepResult(step_name, False, 0.0, None, error=last_error, retries=attempt - 1)

    def get_health(self) -> Dict[str, Any]:
        if not self._history:
            return {"status": "no_runs", "success_rate": 1.0, "avg_latency_ms": 0.0}

        recent = self._history[-20:]
        success_rate = sum(1 for r in recent if r.success) / len(recent)
        avg_latency = sum(r.total_latency_ms for r in recent) / len(recent)

        return {
            "status": "healthy" if success_rate >= 0.8 else "degraded",
            "success_rate": round(success_rate, 4),
            "avg_latency_ms": round(avg_latency, 3),
            "total_runs": len(self._history),
            "recent_failures": [r.pipeline_name for r in recent if not r.success][-5:],
        }

    def recent_results(self, n: int = 10) -> List[PipelineResult]:
        return list(reversed(self._history))[:n]
