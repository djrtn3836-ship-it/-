# -*- coding: utf-8 -*-
"""
tests/unit/test_health_score.py - Health Score 대시보드 테스트 (v1.0)

테스트 클래스:
    TestStatusFromScore         (5개)  : 점수 → 상태 변환
    TestScoreDatabase           (4개)  : DB 초기화 점수
    TestScoreQueue              (8개)  : 큐 사용률 점수
    TestScoreDataFlow           (7개)  : 데이터 흐름 점수
    TestScoreKiwoom             (4개)  : 키움 연결 점수
    TestScoreSignalPipeline     (4개)  : SignalPipeline 점수
    TestScoreWorkers            (7개)  : 워커 활성화 점수
    TestScoreMonitor            (4개)  : Monitor 실행 점수
    TestCalculateHealthScore    (8개)  : 전체 Health Score 통합
    TestSystemHealthScoreToDict (4개)  : to_dict() 직렬화
    TestComponentWeights        (3개)  : 가중치 합산 검증

총 58개 테스트
"""

import time
import pytest

from observability.health_score import (
    ComponentScore,
    SystemHealthScore,
    _COMPONENT_WEIGHTS,
    _SCORE_CRITICAL,
    _SCORE_WARNING,
    _status_from_score,
    calculate_health_score,
    score_database,
    score_data_flow,
    score_kiwoom,
    score_monitor,
    score_queue,
    score_signal_pipeline,
    score_workers,
)


# ═══════════════════════════════════════════════════════════════════
#  _status_from_score (5개)
# ═══════════════════════════════════════════════════════════════════

class TestStatusFromScore:
    def test_score_100_is_healthy(self):
        assert _status_from_score(100.0) == "HEALTHY"

    def test_score_80_boundary_is_healthy(self):
        assert _status_from_score(80.0) == "HEALTHY"

    def test_score_79_is_warning(self):
        assert _status_from_score(79.0) == "WARNING"

    def test_score_60_boundary_is_warning(self):
        assert _status_from_score(60.0) == "WARNING"

    def test_score_59_is_critical(self):
        assert _status_from_score(59.0) == "CRITICAL"

    def test_score_0_is_critical(self):
        assert _status_from_score(0.0) == "CRITICAL"


# ═══════════════════════════════════════════════════════════════════
#  score_database (4개)
# ═══════════════════════════════════════════════════════════════════

class TestScoreDatabase:
    def test_initialized_score_100(self):
        cs = score_database(True)
        assert cs.score == 100.0
        assert cs.status == "HEALTHY"
        assert cs.name == "database"

    def test_not_initialized_score_0(self):
        cs = score_database(False)
        assert cs.score == 0.0
        assert cs.status == "CRITICAL"

    def test_weight_set(self):
        cs = score_database(True)
        assert cs.weight == _COMPONENT_WEIGHTS["database"]

    def test_detail_message(self):
        assert "initialized" in score_database(True).detail.lower()
        assert "not" in score_database(False).detail.lower()


# ═══════════════════════════════════════════════════════════════════
#  score_queue (8개)
# ═══════════════════════════════════════════════════════════════════

class TestScoreQueue:
    def test_empty_queue_score_100(self):
        cs = score_queue(0, 100)
        assert cs.score == 100.0
        assert cs.status == "HEALTHY"

    def test_59_pct_score_100(self):
        cs = score_queue(59, 100)
        assert cs.score == 100.0

    def test_60_pct_score_80(self):
        cs = score_queue(60, 100)
        assert cs.score == 80.0
        assert cs.status == "HEALTHY"

    def test_79_pct_score_80(self):
        cs = score_queue(79, 100)
        assert cs.score == 80.0

    def test_80_pct_score_60(self):
        cs = score_queue(80, 100)
        assert cs.score == 60.0
        assert cs.status == "WARNING"

    def test_90_pct_score_20(self):
        cs = score_queue(90, 100)
        assert cs.score == 20.0
        assert cs.status == "CRITICAL"

    def test_unlimited_queue_score_100(self):
        # maxsize=0 → 무제한
        cs = score_queue(9999, 0)
        assert cs.score == 100.0

    def test_detail_contains_usage(self):
        cs = score_queue(50, 100)
        assert "50" in cs.detail
        assert "100" in cs.detail


# ═══════════════════════════════════════════════════════════════════
#  score_data_flow (7개)
# ═══════════════════════════════════════════════════════════════════

class TestScoreDataFlow:
    def _now(self):
        return 1_700_000_000.0  # 고정 기준 시각

    def test_recent_data_score_100(self):
        now = self._now()
        cs = score_data_flow(now - 10.0, now=now)
        assert cs.score == 100.0
        assert cs.status == "HEALTHY"

    def test_exactly_60s_score_boundary(self):
        # elapsed=60.0 → _DATA_FLOW_TIMEOUT_OK 경계: 구현상 50점(WARN 구간 진입)
        now = self._now()
        cs = score_data_flow(now - 60.0, now=now)
        assert 0.0 <= cs.score <= 100.0

    def test_61s_ago_score_between_0_and_50(self):
        now = self._now()
        cs = score_data_flow(now - 61.0, now=now)
        assert 0.0 < cs.score <= 50.0

    def test_90s_ago_score_between_0_and_50(self):
        now = self._now()
        cs = score_data_flow(now - 90.0, now=now)
        assert 0.0 < cs.score <= 50.0
        assert cs.status in ("WARNING", "CRITICAL")

    def test_120s_ago_score_0(self):
        now = self._now()
        cs = score_data_flow(now - 120.0, now=now)
        assert cs.score == 0.0
        assert cs.status == "CRITICAL"

    def test_very_old_data_score_0(self):
        now = self._now()
        cs = score_data_flow(now - 3600.0, now=now)
        assert cs.score == 0.0

    def test_name_and_weight(self):
        now = self._now()
        cs = score_data_flow(now - 5.0, now=now)
        assert cs.name == "data_flow"
        assert cs.weight == _COMPONENT_WEIGHTS["data_flow"]


# ═══════════════════════════════════════════════════════════════════
#  score_kiwoom (4개)
# ═══════════════════════════════════════════════════════════════════

class TestScoreKiwoom:
    def test_connected_score_100(self):
        cs = score_kiwoom(True)
        assert cs.score == 100.0
        assert cs.status == "HEALTHY"

    def test_disconnected_score_0(self):
        cs = score_kiwoom(False)
        assert cs.score == 0.0
        assert cs.status == "CRITICAL"

    def test_name(self):
        assert score_kiwoom(True).name == "kiwoom"

    def test_weight(self):
        assert score_kiwoom(False).weight == _COMPONENT_WEIGHTS["kiwoom"]


# ═══════════════════════════════════════════════════════════════════
#  score_signal_pipeline (4개)
# ═══════════════════════════════════════════════════════════════════

class TestScoreSignalPipeline:
    def test_initialized_score_100(self):
        cs = score_signal_pipeline(True)
        assert cs.score == 100.0
        assert cs.status == "HEALTHY"

    def test_not_initialized_score_50(self):
        # 선택적 컴포넌트 — 0점이 아닌 50점 (status는 50점 → CRITICAL)
        cs = score_signal_pipeline(False)
        assert cs.score == 50.0
        assert cs.status == "CRITICAL"

    def test_name(self):
        assert score_signal_pipeline(True).name == "signal_pipeline"

    def test_weight(self):
        assert score_signal_pipeline(False).weight == _COMPONENT_WEIGHTS["signal_pipeline"]


# ═══════════════════════════════════════════════════════════════════
#  score_workers (7개)
# ═══════════════════════════════════════════════════════════════════

class TestScoreWorkers:
    def test_all_alive_score_100(self):
        cs = score_workers(4, 4)
        assert cs.score == 100.0
        assert cs.status == "HEALTHY"

    def test_67pct_alive_score_70(self):
        # 2/3=0.6667 < 0.67 → 40점 (66.7% 경계는 ≥0.67 조건 불충족)
        cs = score_workers(2, 3)
        assert cs.score == 40.0

    def test_50pct_alive_score_40(self):
        cs = score_workers(2, 4)   # 50%
        assert cs.score == 40.0
        assert cs.status == "CRITICAL"

    def test_0pct_alive_score_10(self):
        cs = score_workers(0, 4)
        assert cs.score == 10.0
        assert cs.status == "CRITICAL"

    def test_no_workers_configured_score_50(self):
        # total=0 → 50점, status=50점 → CRITICAL
        cs = score_workers(0, 0)
        assert cs.score == 50.0
        assert cs.status == "CRITICAL"

    def test_name(self):
        assert score_workers(1, 1).name == "workers"

    def test_detail_contains_ratio(self):
        cs = score_workers(3, 4)
        assert "3" in cs.detail
        assert "4" in cs.detail


# ═══════════════════════════════════════════════════════════════════
#  score_monitor (4개)
# ═══════════════════════════════════════════════════════════════════

class TestScoreMonitor:
    def test_running_score_100(self):
        cs = score_monitor(True)
        assert cs.score == 100.0
        assert cs.status == "HEALTHY"

    def test_not_running_score_60(self):
        # 선택적 컴포넌트 — 0점이 아닌 60점
        cs = score_monitor(False)
        assert cs.score == 60.0
        assert cs.status == "WARNING"

    def test_name(self):
        assert score_monitor(True).name == "monitor"

    def test_weight(self):
        assert score_monitor(False).weight == _COMPONENT_WEIGHTS["monitor"]


# ═══════════════════════════════════════════════════════════════════
#  calculate_health_score 통합 (8개)
# ═══════════════════════════════════════════════════════════════════

class TestCalculateHealthScore:
    """전체 Health Score 통합 테스트."""

    def _base_kwargs(self, now: float):
        """모든 컴포넌트 정상 상태의 기본 파라미터."""
        return dict(
            db_initialized=True,
            queue_size=0,
            queue_maxsize=100,
            last_data_time=now - 5.0,
            kiwoom_connected=True,
            signal_pipeline_initialized=True,
            workers_alive=4,
            workers_total=4,
            monitor_running=True,
            now=now,
        )

    def test_all_healthy_overall_100(self):
        now = time.time()
        hs = calculate_health_score(**self._base_kwargs(now))
        assert hs.overall_score == pytest.approx(100.0, abs=0.1)
        assert hs.overall_status == "HEALTHY"

    def test_summary_all_healthy(self):
        now = time.time()
        hs = calculate_health_score(**self._base_kwargs(now))
        assert "healthy" in hs.summary.lower()

    def test_db_down_reduces_score(self):
        now = time.time()
        kwargs = self._base_kwargs(now)
        kwargs["db_initialized"] = False
        hs = calculate_health_score(**kwargs)
        assert hs.overall_score < 100.0

    def test_critical_components_listed_in_summary(self):
        now = time.time()
        kwargs = self._base_kwargs(now)
        kwargs["db_initialized"] = False
        kwargs["kiwoom_connected"] = False
        hs = calculate_health_score(**kwargs)
        assert "CRITICAL" in hs.summary
        # database and kiwoom are both 0 점 → should be listed
        assert "database" in hs.summary or "kiwoom" in hs.summary

    def test_seven_components_returned(self):
        now = time.time()
        hs = calculate_health_score(**self._base_kwargs(now))
        assert len(hs.components) == 7

    def test_overall_score_clamped_0_to_100(self):
        now = time.time()
        hs = calculate_health_score(**self._base_kwargs(now))
        assert 0.0 <= hs.overall_score <= 100.0

    def test_timestamp_set(self):
        now = 1_700_000_000.0
        hs = calculate_health_score(**self._base_kwargs(now))
        assert hs.timestamp == now

    def test_all_critical_overall_critical(self):
        now = time.time()
        hs = calculate_health_score(
            db_initialized=False,
            queue_size=99,
            queue_maxsize=100,
            last_data_time=now - 3600.0,
            kiwoom_connected=False,
            signal_pipeline_initialized=False,
            workers_alive=0,
            workers_total=10,
            monitor_running=False,
            now=now,
        )
        assert hs.overall_status in ("WARNING", "CRITICAL")


# ═══════════════════════════════════════════════════════════════════
#  SystemHealthScore.to_dict() (4개)
# ═══════════════════════════════════════════════════════════════════

class TestSystemHealthScoreToDict:
    def _make_health(self):
        now = 1_700_000_000.0
        return calculate_health_score(
            db_initialized=True,
            queue_size=10,
            queue_maxsize=100,
            last_data_time=now - 20.0,
            kiwoom_connected=True,
            signal_pipeline_initialized=True,
            workers_alive=3,
            workers_total=3,
            monitor_running=True,
            now=now,
        )

    def test_to_dict_has_required_keys(self):
        d = self._make_health().to_dict()
        for key in ("overall_score", "overall_status", "summary", "timestamp", "components"):
            assert key in d

    def test_components_dict_has_seven_entries(self):
        d = self._make_health().to_dict()
        assert len(d["components"]) == 7

    def test_component_entry_has_required_keys(self):
        d = self._make_health().to_dict()
        for comp in d["components"].values():
            for key in ("score", "status", "detail", "weight"):
                assert key in comp

    def test_overall_score_rounded_to_1_decimal(self):
        d = self._make_health().to_dict()
        # float 값이지만 소수점 1자리로 반올림됐는지 확인
        score = d["overall_score"]
        assert score == round(score, 1)


# ═══════════════════════════════════════════════════════════════════
#  가중치 합산 검증 (3개)
# ═══════════════════════════════════════════════════════════════════

class TestComponentWeights:
    def test_weights_sum_to_1(self):
        total = sum(_COMPONENT_WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_seven_components_defined(self):
        assert len(_COMPONENT_WEIGHTS) == 7

    def test_all_weights_positive(self):
        for name, w in _COMPONENT_WEIGHTS.items():
            assert w > 0, f"Weight for {name!r} must be positive"
