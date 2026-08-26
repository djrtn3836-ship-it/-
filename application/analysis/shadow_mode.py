# -*- coding: utf-8 -*-
"""
application/analysis/shadow_mode.py - Shadow Mode 실시간 전략 평가 레이어 v1.0

Shadow Mode란?
    신규(실험적) 전략을 실거래 없이 실제 시장 데이터로 평가하는 레이어.
    기존 프로덕션 파이프라인과 병렬로 실행되어 신규 전략의 결과를
    기록·비교하되, 실제 주문은 절대 발생시키지 않는다.

주요 기능:
    - ShadowRunner: 신규 전략을 shadow로 실행하고 결과를 ShadowRecord에 저장
    - ShadowRecord: 프로덕션 vs 섀도우 결과 비교 DTO
    - ShadowEvaluator: 누적 성과 집계 (일치율, 신뢰도 분포, 수익성 지표)
    - ShadowRegistry: 다수의 shadow 전략을 이름으로 관리

사용 방법:
    runner = ShadowRunner("new_strategy_v2", new_pipeline)
    record = await runner.run(data, production_signal)
    evaluator.record(record)
    summary = evaluator.summary()

설계 원칙:
    - Shadow 실행 실패는 예외를 삼키고 ShadowRecord.error 필드에 기록
    - 프로덕션 파이프라인에 절대 영향 없음 (fire-and-forget 가능)
    - 순수 함수 + 불변 DTO 기반 (테스트 용이)
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

from core.logger import setup_logger
from domain.models.signal import Action, Signal

logger = setup_logger("shadow_mode")

# ─── 타입 별칭 ──────────────────────────────────────────────────────
# Shadow 실행기가 호출할 수 있는 async callable: (data) → Signal
ShadowCallable = Callable[[Dict[str, Any]], Coroutine[Any, Any, Signal]]


# ═══════════════════════════════════════════════════════════════════
#  DTO
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ShadowRecord:
    """프로덕션 시그널과 섀도우 시그널의 비교 기록.

    Attributes:
        strategy_name: 섀도우 전략 이름
        ticker: 종목 코드
        production_action: 프로덕션 파이프라인의 Action
        shadow_action: 섀도우 전략의 Action
        production_score: 프로덕션 최종 스코어
        shadow_score: 섀도우 최종 스코어
        production_confidence: 프로덕션 신뢰도
        shadow_confidence: 섀도우 신뢰도
        agreement: 두 시그널의 Action이 동일한지 여부
        latency_ms: 섀도우 실행 소요 시간 (ms)
        error: 섀도우 실행 중 발생한 예외 메시지 (None이면 정상)
        timestamp: 기록 생성 시각
    """
    strategy_name: str
    ticker: str
    production_action: Action
    shadow_action: Action
    production_score: float
    shadow_score: float
    production_confidence: float
    shadow_confidence: float
    agreement: bool
    latency_ms: float
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """JSON 직렬화 가능한 딕셔너리로 변환."""
        return {
            "strategy_name": self.strategy_name,
            "ticker": self.ticker,
            "production_action": self.production_action.value,
            "shadow_action": self.shadow_action.value,
            "production_score": round(self.production_score, 4),
            "shadow_score": round(self.shadow_score, 4),
            "production_confidence": round(self.production_confidence, 4),
            "shadow_confidence": round(self.shadow_confidence, 4),
            "agreement": self.agreement,
            "latency_ms": round(self.latency_ms, 2),
            "error": self.error,
            "timestamp": self.timestamp,
        }


# ═══════════════════════════════════════════════════════════════════
#  ShadowRunner
# ═══════════════════════════════════════════════════════════════════

class ShadowRunner:
    """단일 섀도우 전략을 실행하고 ShadowRecord를 생성합니다.

    Args:
        strategy_name: 이 runner를 식별하는 고유 이름
        shadow_fn: async callable — data dict를 받아 Signal을 반환
        timeout: 섀도우 실행 최대 허용 시간 (초, 기본 5.0)
    """

    def __init__(
        self,
        strategy_name: str,
        shadow_fn: ShadowCallable,
        timeout: float = 5.0,
    ) -> None:
        if not strategy_name or not strategy_name.strip():
            raise ValueError("strategy_name must not be empty")
        if timeout <= 0:
            raise ValueError(f"timeout must be > 0, got {timeout}")
        self.strategy_name = strategy_name
        self._shadow_fn = shadow_fn
        self._timeout = timeout

    async def run(
        self,
        data: Dict[str, Any],
        production_signal: Signal,
    ) -> ShadowRecord:
        """섀도우 전략을 실행하고 프로덕션 시그널과 비교한 ShadowRecord 반환.

        섀도우 실행이 실패(예외/타임아웃)해도 ShadowRecord.error 필드에
        기록될 뿐 예외를 외부로 전파하지 않는다.

        Args:
            data: 원본 tick 데이터 (signal_pipeline.process()와 동일)
            production_signal: 프로덕션 파이프라인이 이미 생성한 Signal

        Returns:
            ShadowRecord: 비교 기록
        """
        ticker = production_signal.ticker
        t_start = time.perf_counter()
        shadow_signal: Optional[Signal] = None
        error_msg: Optional[str] = None

        try:
            shadow_signal = await asyncio.wait_for(
                self._shadow_fn(data), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            error_msg = f"Shadow timeout after {self._timeout}s"
            logger.warning(
                "[ShadowMode] %s | %s | timeout", self.strategy_name, ticker
            )
        except Exception as exc:  # noqa: BLE001
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "[ShadowMode] %s | %s | error: %s",
                self.strategy_name, ticker, error_msg,
            )

        latency_ms = (time.perf_counter() - t_start) * 1000.0

        # 에러 시 fallback shadow 값
        if shadow_signal is None:
            shadow_action = Action.HOLD
            shadow_score = 0.0
            shadow_confidence = 0.0
        else:
            shadow_action = shadow_signal.action
            shadow_score = shadow_signal.score
            shadow_confidence = shadow_signal.confidence

        agreement = production_signal.action == shadow_action

        logger.debug(
            "[ShadowMode] %s | %s | prod=%s shadow=%s agree=%s lat=%.1fms",
            self.strategy_name, ticker,
            production_signal.action.value, shadow_action.value,
            agreement, latency_ms,
        )

        return ShadowRecord(
            strategy_name=self.strategy_name,
            ticker=ticker,
            production_action=production_signal.action,
            shadow_action=shadow_action,
            production_score=production_signal.score,
            shadow_score=shadow_score,
            production_confidence=production_signal.confidence,
            shadow_confidence=shadow_confidence,
            agreement=agreement,
            latency_ms=latency_ms,
            error=error_msg,
        )


# ═══════════════════════════════════════════════════════════════════
#  ShadowEvaluator
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ShadowSummary:
    """ShadowEvaluator 누적 성과 요약 DTO.

    Attributes:
        strategy_name: 전략 이름
        total: 총 평가 횟수
        agreements: 프로덕션과 일치한 횟수
        errors: 에러 발생 횟수
        agreement_rate: 일치율 (0~1)
        avg_shadow_confidence: 평균 섀도우 신뢰도
        avg_latency_ms: 평균 실행 지연 시간 (ms)
        action_counts: Action별 섀도우 발생 횟수
    """
    strategy_name: str
    total: int
    agreements: int
    errors: int
    agreement_rate: float
    avg_shadow_confidence: float
    avg_latency_ms: float
    action_counts: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "total": self.total,
            "agreements": self.agreements,
            "errors": self.errors,
            "agreement_rate": round(self.agreement_rate, 4),
            "avg_shadow_confidence": round(self.avg_shadow_confidence, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "action_counts": self.action_counts,
        }


class ShadowEvaluator:
    """ShadowRecord를 누적 집계해 전략 성과를 평가합니다.

    Args:
        strategy_name: 평가 대상 전략 이름
        max_records: 보관할 최대 기록 수 (오래된 기록은 자동 삭제, 기본 1000)
    """

    def __init__(self, strategy_name: str, max_records: int = 1000) -> None:
        if max_records <= 0:
            raise ValueError(f"max_records must be > 0, got {max_records}")
        self.strategy_name = strategy_name
        self._max_records = max_records
        self._records: List[ShadowRecord] = []

    # ── 공개 API ──────────────────────────────────────────────────

    def record(self, shadow_record: ShadowRecord) -> None:
        """ShadowRecord를 추가합니다. max_records 초과 시 가장 오래된 기록 제거."""
        self._records.append(shadow_record)
        if len(self._records) > self._max_records:
            self._records.pop(0)

    def summary(self) -> ShadowSummary:
        """누적 기록 기반 성과 요약을 반환합니다."""
        total = len(self._records)
        if total == 0:
            return ShadowSummary(
                strategy_name=self.strategy_name,
                total=0,
                agreements=0,
                errors=0,
                agreement_rate=0.0,
                avg_shadow_confidence=0.0,
                avg_latency_ms=0.0,
                action_counts={},
            )

        agreements = sum(1 for r in self._records if r.agreement)
        errors = sum(1 for r in self._records if r.error is not None)
        agreement_rate = agreements / total
        avg_confidence = sum(r.shadow_confidence for r in self._records) / total
        avg_latency = sum(r.latency_ms for r in self._records) / total

        action_counts: Dict[str, int] = {}
        for r in self._records:
            key = r.shadow_action.value
            action_counts[key] = action_counts.get(key, 0) + 1

        return ShadowSummary(
            strategy_name=self.strategy_name,
            total=total,
            agreements=agreements,
            errors=errors,
            agreement_rate=agreement_rate,
            avg_shadow_confidence=avg_confidence,
            avg_latency_ms=avg_latency,
            action_counts=action_counts,
        )

    def clear(self) -> None:
        """누적 기록 초기화."""
        self._records.clear()

    @property
    def record_count(self) -> int:
        """현재 보관 중인 기록 수."""
        return len(self._records)

    def recent(self, n: int = 10) -> List[ShadowRecord]:
        """가장 최근 n개 기록 반환."""
        return list(self._records[-n:])


# ═══════════════════════════════════════════════════════════════════
#  ShadowRegistry
# ═══════════════════════════════════════════════════════════════════

class ShadowRegistry:
    """다수의 Shadow Runner/Evaluator 쌍을 이름으로 관리하는 레지스트리.

    사용 방법:
        registry = ShadowRegistry()
        registry.register("new_v2", new_pipeline.process)
        records = await registry.run_all(data, production_signal)
        summaries = registry.all_summaries()
    """

    def __init__(self) -> None:
        self._runners: Dict[str, ShadowRunner] = {}
        self._evaluators: Dict[str, ShadowEvaluator] = {}

    def register(
        self,
        strategy_name: str,
        shadow_fn: ShadowCallable,
        timeout: float = 5.0,
        max_records: int = 1000,
    ) -> None:
        """새 섀도우 전략을 등록합니다.

        Args:
            strategy_name: 전략 식별 이름 (고유해야 함)
            shadow_fn: async callable — data dict → Signal
            timeout: 실행 타임아웃 (초)
            max_records: 평가기 최대 보관 기록 수
        """
        if strategy_name in self._runners:
            logger.warning("[ShadowRegistry] '%s' already registered; overwriting", strategy_name)
        self._runners[strategy_name] = ShadowRunner(strategy_name, shadow_fn, timeout)
        self._evaluators[strategy_name] = ShadowEvaluator(strategy_name, max_records)
        logger.info("[ShadowRegistry] Registered shadow strategy: %s", strategy_name)

    def unregister(self, strategy_name: str) -> bool:
        """전략 등록을 해제합니다. 존재하지 않으면 False 반환."""
        if strategy_name not in self._runners:
            return False
        del self._runners[strategy_name]
        del self._evaluators[strategy_name]
        logger.info("[ShadowRegistry] Unregistered shadow strategy: %s", strategy_name)
        return True

    async def run_all(
        self,
        data: Dict[str, Any],
        production_signal: Signal,
    ) -> List[ShadowRecord]:
        """등록된 모든 섀도우 전략을 병렬로 실행합니다.

        Args:
            data: 원본 tick 데이터
            production_signal: 프로덕션 파이프라인 결과 Signal

        Returns:
            List[ShadowRecord]: 각 전략의 실행 결과 목록 (순서 보장 안 됨)
        """
        if not self._runners:
            return []

        tasks = [
            runner.run(data, production_signal)
            for runner in self._runners.values()
        ]
        records: List[ShadowRecord] = await asyncio.gather(*tasks, return_exceptions=False)

        # 결과를 평가기에 기록
        for record in records:
            evaluator = self._evaluators.get(record.strategy_name)
            if evaluator is not None:
                evaluator.record(record)

        return list(records)

    async def run_one(
        self,
        strategy_name: str,
        data: Dict[str, Any],
        production_signal: Signal,
    ) -> Optional[ShadowRecord]:
        """특정 전략 하나만 실행합니다. 등록되지 않은 이름이면 None 반환."""
        runner = self._runners.get(strategy_name)
        if runner is None:
            return None
        record = await runner.run(data, production_signal)
        self._evaluators[strategy_name].record(record)
        return record

    def summary(self, strategy_name: str) -> Optional[ShadowSummary]:
        """특정 전략의 성과 요약. 등록되지 않은 이름이면 None 반환."""
        evaluator = self._evaluators.get(strategy_name)
        return evaluator.summary() if evaluator else None

    def all_summaries(self) -> Dict[str, ShadowSummary]:
        """모든 등록 전략의 성과 요약 딕셔너리."""
        return {name: ev.summary() for name, ev in self._evaluators.items()}

    @property
    def registered_names(self) -> List[str]:
        """등록된 전략 이름 목록."""
        return list(self._runners.keys())

    def __len__(self) -> int:
        return len(self._runners)

    def __contains__(self, strategy_name: str) -> bool:
        return strategy_name in self._runners
