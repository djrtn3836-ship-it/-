# -*- coding: utf-8 -*-
"""
observability/health_score.py - V10 Health Score 대시보드 v1.0

컴포넌트별 건강도를 0~100점으로 정량화.
각 컴포넌트 점수의 가중 평균이 시스템 전체 Health Score.

점수 기준:
    100 = 완전 정상
    80~99 = 경미한 이상
    60~79 = 주의 필요 (WARNING)
    0~59  = 심각 (CRITICAL)

컴포넌트 목록:
    - database:       DB 초기화 여부
    - queue:          메시지 큐 사용률 (0~100%, 90% 이상 위험)
    - data_flow:      마지막 데이터 수신 시간 (최근 60초 이내 정상)
    - kiwoom:         키움 API 연결 상태
    - signal_pipeline: SignalPipeline 초기화 여부
    - workers:        워커 활성화 비율
    - monitor:        DailyMonitor 실행 상태
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ─── 점수 임계값 ─────────────────────────────────────────────────
_SCORE_CRITICAL = 60    # 이하: CRITICAL
_SCORE_WARNING = 80     # 이하: WARNING
_SCORE_HEALTHY = 100    # 정상

# ─── 컴포넌트 가중치 (합 = 1.0) ─────────────────────────────────
_COMPONENT_WEIGHTS: Dict[str, float] = {
    "database":        0.20,
    "queue":           0.15,
    "data_flow":       0.20,
    "kiwoom":          0.15,
    "signal_pipeline": 0.10,
    "workers":         0.10,
    "monitor":         0.10,
}

# ─── 데이터 흐름 타임아웃 (초) ───────────────────────────────────
_DATA_FLOW_TIMEOUT_OK = 60.0      # 60초 이내 = 100점
_DATA_FLOW_TIMEOUT_WARN = 120.0   # 60~120초 = 50점
# 120초 이상 = 0점


@dataclass
class ComponentScore:
    """단일 컴포넌트 건강도 점수 DTO

    Attributes:
        name: 컴포넌트 이름
        score: 건강도 점수 (0~100)
        status: 상태 문자열 ("HEALTHY", "WARNING", "CRITICAL")
        detail: 점수 산출 근거 메시지
        weight: 전체 점수 계산에 사용된 가중치
    """
    name: str
    score: float
    status: str
    detail: str
    weight: float = 0.0


@dataclass
class SystemHealthScore:
    """시스템 전체 Health Score 집계 결과

    Attributes:
        overall_score: 가중 평균 전체 점수 (0~100)
        overall_status: 전체 상태 ("HEALTHY", "WARNING", "CRITICAL")
        components: 개별 컴포넌트 점수 목록
        timestamp: 계산 시각 (Unix timestamp)
        summary: 한 줄 요약 문자열
    """
    overall_score: float
    overall_status: str
    components: List[ComponentScore]
    timestamp: float = field(default_factory=time.time)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """JSON 직렬화 가능한 딕셔너리로 변환."""
        return {
            "overall_score": round(self.overall_score, 1),
            "overall_status": self.overall_status,
            "summary": self.summary,
            "timestamp": self.timestamp,
            "components": {
                c.name: {
                    "score": round(c.score, 1),
                    "status": c.status,
                    "detail": c.detail,
                    "weight": c.weight,
                }
                for c in self.components
            },
        }


# ═══════════════════════════════════════════════════════════════════
#  점수 산출 함수 (순수 함수 — 테스트 용이)
# ═══════════════════════════════════════════════════════════════════

def _status_from_score(score: float) -> str:
    """점수 → 상태 문자열 변환."""
    if score >= _SCORE_WARNING:
        return "HEALTHY"
    if score >= _SCORE_CRITICAL:
        return "WARNING"
    return "CRITICAL"


def score_database(db_initialized: bool) -> ComponentScore:
    """DB 초기화 여부 → 건강도 점수.

    Args:
        db_initialized: DatabaseManager 초기화 완료 여부

    Returns:
        ComponentScore: 100 (정상) / 0 (미초기화)
    """
    score = 100.0 if db_initialized else 0.0
    detail = "DB initialized" if db_initialized else "DB not initialized"
    return ComponentScore(
        name="database",
        score=score,
        status=_status_from_score(score),
        detail=detail,
        weight=_COMPONENT_WEIGHTS["database"],
    )


def score_queue(queue_size: int, queue_maxsize: int) -> ComponentScore:
    """메시지 큐 사용률 → 건강도 점수.

    사용률:
        0~59% → 100점
        60~79% → 80점
        80~89% → 60점
        90~100% → 20점

    Args:
        queue_size: 현재 큐 사이즈
        queue_maxsize: 큐 최대 용량 (0이면 무제한)

    Returns:
        ComponentScore
    """
    if queue_maxsize <= 0:
        usage = 0.0
    else:
        usage = queue_size / queue_maxsize * 100.0

    if usage < 60:
        score = 100.0
    elif usage < 80:
        score = 80.0
    elif usage < 90:
        score = 60.0
    else:
        score = 20.0

    detail = f"Queue usage: {usage:.1f}% ({queue_size}/{queue_maxsize})"
    return ComponentScore(
        name="queue",
        score=score,
        status=_status_from_score(score),
        detail=detail,
        weight=_COMPONENT_WEIGHTS["queue"],
    )


def score_data_flow(last_data_time: float, now: Optional[float] = None) -> ComponentScore:
    """마지막 데이터 수신 시간 → 건강도 점수.

    경과 시간:
        0~60초  → 100점 (정상)
        60~120초 → 50점 (주의)
        120초~  → 0점 (위험)

    Args:
        last_data_time: 마지막 데이터 수신 Unix timestamp
        now: 현재 시각 (None이면 time.time())

    Returns:
        ComponentScore
    """
    now = now or time.time()
    elapsed = now - last_data_time

    if elapsed < _DATA_FLOW_TIMEOUT_OK:
        score = 100.0
    elif elapsed < _DATA_FLOW_TIMEOUT_WARN:
        # 60~120초 → 선형 감소 100→0
        score = 100.0 * (1.0 - (elapsed - _DATA_FLOW_TIMEOUT_OK) / _DATA_FLOW_TIMEOUT_OK)
        score = max(0.0, min(50.0, score))
    else:
        score = 0.0

    detail = f"Last data {elapsed:.1f}s ago"
    return ComponentScore(
        name="data_flow",
        score=score,
        status=_status_from_score(score),
        detail=detail,
        weight=_COMPONENT_WEIGHTS["data_flow"],
    )


def score_kiwoom(connected: bool) -> ComponentScore:
    """키움 API 연결 상태 → 건강도 점수.

    Args:
        connected: 연결 여부

    Returns:
        ComponentScore: 100 (연결) / 0 (미연결)
    """
    score = 100.0 if connected else 0.0
    detail = "Kiwoom connected" if connected else "Kiwoom disconnected"
    return ComponentScore(
        name="kiwoom",
        score=score,
        status=_status_from_score(score),
        detail=detail,
        weight=_COMPONENT_WEIGHTS["kiwoom"],
    )


def score_signal_pipeline(initialized: bool) -> ComponentScore:
    """SignalPipeline 초기화 여부 → 건강도 점수.

    Args:
        initialized: SignalPipeline 인스턴스 존재 여부

    Returns:
        ComponentScore: 100 / 50 (미초기화)
    """
    score = 100.0 if initialized else 50.0  # 선택적 컴포넌트라 0점 아님
    detail = "SignalPipeline active" if initialized else "SignalPipeline not initialized"
    return ComponentScore(
        name="signal_pipeline",
        score=score,
        status=_status_from_score(score),
        detail=detail,
        weight=_COMPONENT_WEIGHTS["signal_pipeline"],
    )


def score_workers(alive: int, total: int) -> ComponentScore:
    """워커 활성화 비율 → 건강도 점수.

    비율:
        100%     → 100점
        67~99%  → 70점
        33~66%  → 40점
        0~32%   → 10점

    Args:
        alive: 살아 있는 워커 수
        total: 전체 워커 수

    Returns:
        ComponentScore
    """
    if total <= 0:
        score = 50.0
        detail = "No workers configured"
    else:
        ratio = alive / total
        if ratio >= 1.0:
            score = 100.0
        elif ratio >= 0.67:
            score = 70.0
        elif ratio >= 0.33:
            score = 40.0
        else:
            score = 10.0
        detail = f"Workers: {alive}/{total} alive ({ratio:.0%})"

    return ComponentScore(
        name="workers",
        score=score,
        status=_status_from_score(score),
        detail=detail,
        weight=_COMPONENT_WEIGHTS["workers"],
    )


def score_monitor(running: bool) -> ComponentScore:
    """DailyMonitor 실행 상태 → 건강도 점수.

    Args:
        running: Monitor.is_running() 결과

    Returns:
        ComponentScore: 100 / 60 (중단)
    """
    score = 100.0 if running else 60.0  # Monitor는 선택적
    detail = "DailyMonitor running" if running else "DailyMonitor not running"
    return ComponentScore(
        name="monitor",
        score=score,
        status=_status_from_score(score),
        detail=detail,
        weight=_COMPONENT_WEIGHTS["monitor"],
    )


# ═══════════════════════════════════════════════════════════════════
#  전체 Health Score 계산
# ═══════════════════════════════════════════════════════════════════

def calculate_health_score(
    db_initialized: bool,
    queue_size: int,
    queue_maxsize: int,
    last_data_time: float,
    kiwoom_connected: bool,
    signal_pipeline_initialized: bool,
    workers_alive: int,
    workers_total: int,
    monitor_running: bool,
    now: Optional[float] = None,
) -> SystemHealthScore:
    """전체 시스템 Health Score 계산.

    각 컴포넌트 점수의 가중 평균으로 overall_score 산출.

    Args:
        db_initialized: DB 초기화 여부
        queue_size: 현재 큐 크기
        queue_maxsize: 큐 최대 용량
        last_data_time: 마지막 데이터 수신 시각
        kiwoom_connected: 키움 API 연결 여부
        signal_pipeline_initialized: SignalPipeline 초기화 여부
        workers_alive: 활성 워커 수
        workers_total: 전체 워커 수
        monitor_running: DailyMonitor 실행 여부
        now: 현재 시각 (None이면 time.time())

    Returns:
        SystemHealthScore: 전체 + 개별 컴포넌트 건강도 집계
    """
    now = now or time.time()

    components: List[ComponentScore] = [
        score_database(db_initialized),
        score_queue(queue_size, queue_maxsize),
        score_data_flow(last_data_time, now),
        score_kiwoom(kiwoom_connected),
        score_signal_pipeline(signal_pipeline_initialized),
        score_workers(workers_alive, workers_total),
        score_monitor(monitor_running),
    ]

    # 가중 평균 계산
    total_weight = sum(c.weight for c in components)
    if total_weight > 0:
        overall_score = sum(c.score * c.weight for c in components) / total_weight
    else:
        overall_score = 0.0

    overall_score = max(0.0, min(100.0, overall_score))
    overall_status = _status_from_score(overall_score)

    # 요약 문자열
    critical_components = [c.name for c in components if c.status == "CRITICAL"]
    warning_components = [c.name for c in components if c.status == "WARNING"]

    if critical_components:
        summary = f"CRITICAL: {', '.join(critical_components)}"
    elif warning_components:
        summary = f"WARNING: {', '.join(warning_components)}"
    else:
        summary = "All components healthy"

    return SystemHealthScore(
        overall_score=overall_score,
        overall_status=overall_status,
        components=components,
        timestamp=now,
        summary=summary,
    )
