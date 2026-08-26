# -*- coding: utf-8 -*-
"""
tests/unit/test_ab_framework.py
A/B Testing Framework v1.0 단위 테스트 — 38개

Test Classes:
    TestMathHelpers         ( 8개): _welch_t_stat, _t_cdf, _p_value_two_tail
    TestABVariant           ( 6개): record, to_dict, 클리핑
    TestABTest              (10개): assign, record, analyze, conclude
    TestABTestManager       (10개): create_test, assign_variant, record_result, get_winner
    TestStatisticalValidity ( 4개): 실제 분포 차이 검출 / 차이 없음 기각 불가
"""

import asyncio

import pytest

from application.analysis.ab_framework import (
    ABTest,
    ABTestManager,
    ABVariant,
    StatResult,
    TestStatus,
    _mean,
    _p_value_two_tail,
    _t_cdf,
    _var,
    _welch_t_stat,
)

# ──────────────────────────────────────────────────────────────────────────────
# 공통 헬퍼
# ──────────────────────────────────────────────────────────────────────────────

def _make_variant(name: str = "ctrl", n: int = 0, mu: float = 0.0) -> ABVariant:
    v = ABVariant(name=name, traffic_weight=0.5)
    import random
    random.seed(99)
    for _ in range(n):
        v.record(mu + random.gauss(0, 0.01))
    return v


def _make_test(names=("control", "variant"), min_samples: int = 5) -> ABTest:
    variants = [ABVariant(name=n, traffic_weight=1.0 / len(names)) for n in names]
    return ABTest(name="test_exp", variants=variants, min_samples=min_samples)


def _fresh_manager() -> ABTestManager:
    """독립적인 ABTestManager 인스턴스 반환 (싱글턴 우회)."""
    mgr = object.__new__(ABTestManager)
    # _initialized 플래그 없이 __init__ 직접 호용
    mgr._tests = {}
    mgr._lock = asyncio.Lock()
    return mgr


# ──────────────────────────────────────────────────────────────────────────────
# TestMathHelpers
# ──────────────────────────────────────────────────────────────────────────────

class TestMathHelpers:

    def test_mean_basic(self):
        assert abs(_mean([1.0, 2.0, 3.0]) - 2.0) < 1e-10

    def test_mean_empty(self):
        assert _mean([]) == 0.0

    def test_var_basic(self):
        # [2, 4, 4, 4, 5, 5, 7, 9] → 분산 4.0
        assert abs(_var([2, 4, 4, 4, 5, 5, 7, 9]) - 4.571428, ) < 0.01

    def test_var_single_returns_zero(self):
        assert _var([1.0]) == 0.0

    def test_welch_t_same_distribution(self):
        """동일 분포 → t ≈ 0."""
        a = [0.01] * 20
        b = [0.01] * 20
        t, df = _welch_t_stat(a, b)
        assert abs(t) < 1e-6

    def test_welch_t_different_means(self):
        """평균 차이 있으면 |t| > 0."""
        import random
        random.seed(7)
        a = [0.01 + random.gauss(0, 0.001) for _ in range(30)]
        b = [0.05 + random.gauss(0, 0.001) for _ in range(30)]
        t, df = _welch_t_stat(a, b)
        assert abs(t) > 1.0

    def test_t_cdf_at_zero_is_half(self):
        """P(T ≤ 0) = 0.5 (대칭)."""
        p = _t_cdf(0.0, df=10.0)
        assert abs(p - 0.5) < 0.01

    def test_p_value_large_t_is_small(self):
        """매우 큰 |t|에서 p-value → 0."""
        p = _p_value_two_tail(100.0, df=50.0)
        assert p < 0.001


# ──────────────────────────────────────────────────────────────────────────────
# TestABVariant
# ──────────────────────────────────────────────────────────────────────────────

class TestABVariant:

    def test_record_adds_to_results(self):
        v = ABVariant(name="a")
        v.record(0.02)
        assert v.n == 1
        assert v.results[0] == 0.02

    def test_clip_max(self):
        """1.0 초과 → 1.0 클리핑."""
        v = ABVariant(name="a")
        v.record(5.0)
        assert v.results[0] == 1.0

    def test_clip_min(self):
        """-1.0 미만 → -1.0 클리핑."""
        v = ABVariant(name="a")
        v.record(-5.0)
        assert v.results[0] == -1.0

    def test_mean_and_std(self):
        v = ABVariant(name="a")
        for x in [0.01, 0.02, 0.03]:
            v.record(x)
        assert abs(v.mean - 0.02) < 1e-10
        assert v.std > 0

    def test_to_dict_keys(self):
        v = ABVariant(name="ctrl", traffic_weight=0.4)
        d = v.to_dict()
        for k in ("name", "traffic_weight", "n", "mean", "std", "assignment_count"):
            assert k in d

    def test_assignment_count_increments(self):
        v = ABVariant(name="ctrl")
        v.assignment_count += 1
        assert v.assignment_count == 1


# ──────────────────────────────────────────────────────────────────────────────
# TestABTest
# ──────────────────────────────────────────────────────────────────────────────

class TestABTest:

    def test_assign_deterministic(self):
        """동일 user_id → 항상 동일 변형."""
        t = _make_test()
        r1 = t.assign("user_abc")
        r2 = t.assign("user_abc")
        assert r1 == r2

    def test_assign_returns_valid_variant(self):
        t = _make_test()
        v = t.assign("user_xyz")
        assert v in t.variants

    def test_assign_distributes_traffic(self):
        """1000명 배정 시 두 변형 모두 배정받아야 한다."""
        t = _make_test()
        counts = {"control": 0, "variant": 0}
        for i in range(1000):
            v = t.assign(f"user_{i}")
            counts[v] += 1
        assert counts["control"] > 300
        assert counts["variant"] > 300

    @pytest.mark.asyncio
    async def test_record_adds_result(self):
        t = _make_test()
        await t.record("control", 0.02)
        assert t.variants["control"].n == 1

    @pytest.mark.asyncio
    async def test_record_unknown_variant_ignored(self):
        t = _make_test()
        await t.record("unknown_variant", 0.02)
        assert sum(v.n for v in t.variants.values()) == 0

    def test_analyze_insufficient_samples(self):
        t = _make_test(min_samples=10)
        result = t.analyze()
        assert result.tested is False
        assert "샘플 부족" in result.reason

    def test_analyze_significant_difference(self):
        """실제 평균 차이가 큰 경우 유의미한 차이 검출."""
        import random
        random.seed(42)
        t = _make_test(min_samples=5)
        for _ in range(30):
            t.variants["control"].record(0.001 + random.gauss(0, 0.0005))
            t.variants["variant"].record(0.100 + random.gauss(0, 0.0005))
        result = t.analyze()
        assert result.tested is True
        assert result.significant is True
        assert result.winner == "variant"

    def test_analyze_no_significant_difference(self):
        """동일 분포에서는 유의미한 차이 없음."""
        t = _make_test(min_samples=5)
        for _ in range(20):
            t.variants["control"].record(0.01)
            t.variants["variant"].record(0.01)
        result = t.analyze()
        assert result.winner is None

    def test_conclude_sets_status(self):
        t = _make_test()
        for _ in range(5):
            t.variants["control"].record(0.01)
            t.variants["variant"].record(0.01)
        t.conclude()
        assert t.status == TestStatus.CONCLUDED
        assert t.end_time is not None

    def test_get_status_contains_required_keys(self):
        t = _make_test()
        s = t.get_status()
        for k in ("test_id", "name", "status", "variants", "statistics"):
            assert k in s


# ──────────────────────────────────────────────────────────────────────────────
# TestABTestManager
# ──────────────────────────────────────────────────────────────────────────────

class TestABTestManager:

    def test_create_test_basic(self):
        mgr = _fresh_manager()
        test = mgr.create_test("exp1", ["a", "b"])
        assert test.name == "exp1"
        assert "a" in test.variants
        assert "b" in test.variants

    def test_create_test_custom_split(self):
        mgr = _fresh_manager()
        test = mgr.create_test("exp2", ["a", "b"], traffic_split=[0.3, 0.7])
        assert abs(test.variants["b"].traffic_weight - 0.7) < 1e-9

    def test_create_test_too_few_variants_raises(self):
        mgr = _fresh_manager()
        with pytest.raises(ValueError, match="최소 2개"):
            mgr.create_test("exp3", ["only_one"])

    def test_create_test_split_mismatch_raises(self):
        mgr = _fresh_manager()
        with pytest.raises(ValueError, match="길이"):
            mgr.create_test("exp4", ["a", "b"], traffic_split=[0.5, 0.3, 0.2])

    def test_create_test_no_overwrite(self):
        mgr = _fresh_manager()
        t1 = mgr.create_test("exp5", ["a", "b"])
        t2 = mgr.create_test("exp5", ["x", "y"])   # 덮어쓰기 금지
        assert t1 is t2

    def test_assign_variant_returns_variant_name(self):
        mgr = _fresh_manager()
        mgr.create_test("exp6", ["ctrl", "var"])
        variant = mgr.assign_variant("exp6", "user1")
        assert variant in ("ctrl", "var")

    def test_assign_variant_unknown_test_returns_none(self):
        mgr = _fresh_manager()
        result = mgr.assign_variant("nonexistent", "u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_record_result_ok(self):
        mgr = _fresh_manager()
        mgr.create_test("exp7", ["a", "b"])
        ok = await mgr.record_result("exp7", "a", 0.03)
        assert ok is True

    @pytest.mark.asyncio
    async def test_record_result_unknown_test_returns_false(self):
        mgr = _fresh_manager()
        ok = await mgr.record_result("ghost_test", "a", 0.0)
        assert ok is False

    def test_get_winner_returns_none_when_no_winner(self):
        mgr = _fresh_manager()
        mgr.create_test("exp8", ["a", "b"])
        assert mgr.get_winner("exp8") is None   # 샘플 없음


# ──────────────────────────────────────────────────────────────────────────────
# TestStatisticalValidity
# ──────────────────────────────────────────────────────────────────────────────

class TestStatisticalValidity:

    @pytest.mark.asyncio
    async def test_detects_large_effect(self):
        """효과 크기 큰 실험 → 유의미한 승자 판별."""
        import random
        random.seed(1)
        mgr = _fresh_manager()
        mgr.create_test("large_effect", ["a", "b"], min_samples=10)
        for _ in range(50):
            await mgr.record_result("large_effect", "a", 0.001 + random.gauss(0, 0.0005))
            await mgr.record_result("large_effect", "b", 0.050 + random.gauss(0, 0.0005))
        winner = mgr.get_winner("large_effect")
        assert winner == "b"

    @pytest.mark.asyncio
    async def test_no_winner_when_same(self):
        """동일 분포 → 승자 없음."""
        import random
        random.seed(99)
        mgr = _fresh_manager()
        mgr.create_test("same_dist", ["a", "b"], min_samples=10)
        for _ in range(50):
            v = 0.010 + random.gauss(0, 0.001)
            await mgr.record_result("same_dist", "a", v)
            await mgr.record_result("same_dist", "b", v)
        winner = mgr.get_winner("same_dist")
        assert winner is None

    def test_effect_size_positive_when_significant(self):
        """유의미한 차이 있을 때 effect_size > 0."""
        import random
        random.seed(7)
        t = _make_test(min_samples=5)
        for _ in range(30):
            t.variants["control"].record(0.001 + random.gauss(0, 0.0005))
            t.variants["variant"].record(0.100 + random.gauss(0, 0.0005))
        result = t.analyze()
        assert result.effect_size > 0.0

    def test_conclude_returns_stat_result(self):
        """conclude() 반환값이 StatResult 인스턴스."""
        t = _make_test(min_samples=5)
        for _ in range(10):
            t.variants["control"].record(0.01)
            t.variants["variant"].record(0.02)
        result = t.conclude()
        assert isinstance(result, StatResult)
        assert result.tested is True
