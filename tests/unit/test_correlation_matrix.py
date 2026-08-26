# -*- coding: utf-8 -*-
"""
tests/unit/test_correlation_matrix.py - 실시간 상관행렬 테스트 (v1.0)

테스트 클래스:
    TestPearsonCorrelation          (6개)  : 순수 함수 단위 테스트
    TestRollingCorrelationInit      (4개)  : 초기화 검증
    TestRollingCorrelationAddReturn (6개)  : add_return / add_returns_batch
    TestRollingCorrelationCompute   (7개)  : correlation / correlation_matrix
    TestHighCorrPairs               (4개)  : high_correlation_pairs
    TestDiversificationScore        (7개)  : diversification_score
    TestRollingCorrelationMgmt      (4개)  : remove / clear / tracked
    TestCorrelationPairDTO          (4개)  : CorrelationPair DTO

총 42개 테스트
"""

import math
import pytest

from risk.correlation_matrix import (
    CorrelationPair,
    DiversificationReport,
    RollingCorrelation,
    _align_returns,
    _pearson_correlation,
)


# ─── 헬퍼 ─────────────────────────────────────────────────────────

def _sin_returns(n: int, freq: float = 1.0, amp: float = 0.01) -> list:
    """결정적 사인파 수익률 시계열."""
    return [amp * math.sin(2 * math.pi * freq * i / n) for i in range(n)]


def _add_series(matrix: RollingCorrelation, ticker: str, returns: list) -> None:
    for r in returns:
        matrix.add_return(ticker, r)


# ═══════════════════════════════════════════════════════════════════
#  _pearson_correlation (6개)
# ═══════════════════════════════════════════════════════════════════

class TestPearsonCorrelation:
    def test_identical_series_corr_1(self):
        x = [0.01, 0.02, -0.01, 0.03, 0.00]
        assert _pearson_correlation(x, x) == pytest.approx(1.0, abs=1e-9)

    def test_opposite_series_corr_minus1(self):
        x = [0.01, 0.02, -0.01, 0.03, 0.00]
        neg_x = [-v for v in x]
        result = _pearson_correlation(x, neg_x)
        assert result == pytest.approx(-1.0, abs=1e-9)

    def test_uncorrelated_near_zero(self):
        x = [1, -1, 1, -1, 1]
        y = [1, 1, -1, -1, 0]
        r = _pearson_correlation(x, y)
        assert r is None or abs(r) < 0.5

    def test_constant_returns_none(self):
        x = [0.5] * 10
        y = [0.3] * 10
        assert _pearson_correlation(x, y) is None

    def test_length_mismatch_returns_none(self):
        assert _pearson_correlation([1, 2, 3], [1, 2]) is None

    def test_result_in_range(self):
        x = _sin_returns(20)
        y = _sin_returns(20, freq=2.0)
        r = _pearson_correlation(x, y)
        if r is not None:
            assert -1.0 <= r <= 1.0


# ═══════════════════════════════════════════════════════════════════
#  RollingCorrelation 초기화 (4개)
# ═══════════════════════════════════════════════════════════════════

class TestRollingCorrelationInit:
    def test_default_init(self):
        m = RollingCorrelation()
        assert m.ticker_count == 0

    def test_window_too_small_raises(self):
        with pytest.raises(ValueError):
            RollingCorrelation(window=2)

    def test_invalid_threshold_raises(self):
        with pytest.raises(ValueError):
            RollingCorrelation(high_corr_threshold=0.0)

    def test_threshold_1_valid(self):
        m = RollingCorrelation(high_corr_threshold=1.0)
        assert m is not None


# ═══════════════════════════════════════════════════════════════════
#  add_return / add_returns_batch (6개)
# ═══════════════════════════════════════════════════════════════════

class TestRollingCorrelationAddReturn:
    def test_add_single_return(self):
        m = RollingCorrelation(window=10)
        m.add_return("005930", 0.01)
        assert m.return_count("005930") == 1

    def test_add_multiple_returns(self):
        m = RollingCorrelation(window=10)
        for i in range(5):
            m.add_return("005930", 0.01 * i)
        assert m.return_count("005930") == 5

    def test_window_eviction(self):
        m = RollingCorrelation(window=5)
        for i in range(8):
            m.add_return("A", 0.01 * i)
        assert m.return_count("A") == 5

    def test_add_returns_batch(self):
        m = RollingCorrelation(window=10)
        m.add_returns_batch({"A": 0.01, "B": -0.01, "C": 0.02})
        assert m.ticker_count == 3

    def test_new_ticker_auto_created(self):
        m = RollingCorrelation(window=10)
        m.add_return("NEW", 0.05)
        assert "NEW" in m.tracked_tickers

    def test_max_tickers_limit(self):
        m = RollingCorrelation(window=10, max_tickers=3)
        for i in range(5):
            m.add_return(f"T{i}", 0.01)
        assert m.ticker_count == 3


# ═══════════════════════════════════════════════════════════════════
#  correlation / correlation_matrix (7개)
# ═══════════════════════════════════════════════════════════════════

class TestRollingCorrelationCompute:
    @pytest.fixture
    def matrix(self):
        m = RollingCorrelation(window=30, high_corr_threshold=0.80)
        returns_a = _sin_returns(20)
        returns_b = _sin_returns(20)                 # 동일 → r≈1
        returns_c = [-r for r in _sin_returns(20)]   # 반전 → r≈-1
        _add_series(m, "A", returns_a)
        _add_series(m, "B", returns_b)
        _add_series(m, "C", returns_c)
        return m

    def test_corr_identical_series(self, matrix):
        pair = matrix.correlation("A", "B")
        assert pair is not None
        assert pair.correlation == pytest.approx(1.0, abs=1e-9)

    def test_corr_opposite_series(self, matrix):
        pair = matrix.correlation("A", "C")
        assert pair is not None
        assert pair.correlation == pytest.approx(-1.0, abs=1e-9)

    def test_corr_returns_none_for_unknown(self, matrix):
        assert matrix.correlation("A", "UNKNOWN") is None

    def test_corr_pair_has_correct_fields(self, matrix):
        pair = matrix.correlation("A", "B")
        assert pair.ticker_a == "A"
        assert pair.ticker_b == "B"
        assert pair.n_samples > 0

    def test_corr_matrix_diagonal_is_1(self, matrix):
        cm = matrix.correlation_matrix(["A", "B"])
        assert cm["A"]["A"] == pytest.approx(1.0)
        assert cm["B"]["B"] == pytest.approx(1.0)

    def test_corr_matrix_symmetric(self, matrix):
        cm = matrix.correlation_matrix(["A", "B"])
        assert cm["A"]["B"] == pytest.approx(cm["B"]["A"])

    def test_corr_matrix_no_data_returns_zero(self, matrix):
        cm = matrix.correlation_matrix(["A", "UNKNOWN"])
        assert cm["A"]["UNKNOWN"] == pytest.approx(0.0)


# ═══════════════════════════════════════════════════════════════════
#  high_correlation_pairs (4개)
# ═══════════════════════════════════════════════════════════════════

class TestHighCorrPairs:
    @pytest.fixture
    def matrix(self):
        m = RollingCorrelation(window=30, high_corr_threshold=0.90)
        returns = _sin_returns(20)
        _add_series(m, "A", returns)
        _add_series(m, "B", returns)              # 동일 → 고상관
        _add_series(m, "C", _sin_returns(20, freq=7.3))  # 다른 주파수
        return m

    def test_identical_pair_detected(self, matrix):
        pairs = matrix.high_correlation_pairs(["A", "B"])
        assert len(pairs) == 1
        assert pairs[0].is_high is True

    def test_no_duplicates(self, matrix):
        pairs = matrix.high_correlation_pairs()
        pair_keys = [(min(p.ticker_a, p.ticker_b), max(p.ticker_a, p.ticker_b))
                     for p in pairs]
        assert len(pair_keys) == len(set(pair_keys))

    def test_returns_list(self, matrix):
        result = matrix.high_correlation_pairs()
        assert isinstance(result, list)

    def test_empty_matrix_no_pairs(self):
        m = RollingCorrelation(window=10)
        assert m.high_correlation_pairs() == []


# ═══════════════════════════════════════════════════════════════════
#  diversification_score (7개)
# ═══════════════════════════════════════════════════════════════════

class TestDiversificationScore:
    @pytest.fixture
    def perfect_div(self):
        """반상관 종목 → 높은 분산화 점수."""
        m = RollingCorrelation(window=30)
        returns = _sin_returns(20)
        _add_series(m, "A", returns)
        _add_series(m, "B", [-r for r in returns])
        return m

    @pytest.fixture
    def poor_div(self):
        """동일 종목 → 낮은 분산화 점수."""
        m = RollingCorrelation(window=30)
        returns = _sin_returns(20)
        _add_series(m, "A", returns)
        _add_series(m, "B", returns)
        return m

    def test_score_in_range(self, perfect_div):
        report = perfect_div.diversification_score()
        assert 0.0 <= report.score <= 1.0

    def test_poor_diversification_lower_score(self, poor_div, perfect_div):
        poor = poor_div.diversification_score().score
        good = perfect_div.diversification_score().score
        # 고상관이 분산화 점수가 더 낮아야 함
        assert poor <= good

    def test_single_ticker_score_1(self):
        m = RollingCorrelation(window=10)
        _add_series(m, "A", _sin_returns(10))
        report = m.diversification_score(["A"])
        assert report.score == 1.0

    def test_report_has_required_fields(self, perfect_div):
        d = perfect_div.diversification_score().to_dict()
        for key in ("score", "tickers", "avg_abs_correlation",
                    "high_corr_pairs", "recommendation"):
            assert key in d

    def test_recommendation_not_empty(self, poor_div):
        report = poor_div.diversification_score()
        assert len(report.recommendation) > 0

    def test_empty_matrix_single_ticker(self):
        m = RollingCorrelation(window=10)
        report = m.diversification_score(["A", "B"])
        # 데이터 없으면 계산 불가 → 1.0 또는 0.0 허용
        assert 0.0 <= report.score <= 1.0

    def test_avg_abs_correlation_in_range(self, poor_div):
        report = poor_div.diversification_score()
        assert 0.0 <= report.avg_abs_correlation <= 1.0


# ═══════════════════════════════════════════════════════════════════
#  remove / clear / tracked (4개)
# ═══════════════════════════════════════════════════════════════════

class TestRollingCorrelationMgmt:
    def test_remove_existing_ticker(self):
        m = RollingCorrelation(window=10)
        m.add_return("A", 0.01)
        assert m.remove_ticker("A") is True
        assert "A" not in m.tracked_tickers

    def test_remove_nonexistent_returns_false(self):
        m = RollingCorrelation(window=10)
        assert m.remove_ticker("UNKNOWN") is False

    def test_clear_removes_all(self):
        m = RollingCorrelation(window=10)
        for t in ["A", "B", "C"]:
            m.add_return(t, 0.01)
        m.clear()
        assert m.ticker_count == 0

    def test_tracked_tickers_list(self):
        m = RollingCorrelation(window=10)
        m.add_return("X", 0.01)
        m.add_return("Y", 0.02)
        assert set(m.tracked_tickers) == {"X", "Y"}


# ═══════════════════════════════════════════════════════════════════
#  CorrelationPair DTO (4개)
# ═══════════════════════════════════════════════════════════════════

class TestCorrelationPairDTO:
    def _make(self):
        return CorrelationPair(
            ticker_a="005930",
            ticker_b="000660",
            correlation=0.72,
            n_samples=30,
            is_high=False,
        )

    def test_to_dict_has_required_keys(self):
        d = self._make().to_dict()
        for key in ("ticker_a", "ticker_b", "correlation",
                    "n_samples", "is_high", "timestamp"):
            assert key in d

    def test_correlation_rounded(self):
        d = self._make().to_dict()
        r = d["correlation"]
        assert r == round(r, 4)

    def test_frozen(self):
        pair = self._make()
        with pytest.raises((AttributeError, TypeError)):
            pair.correlation = 0.99  # type: ignore

    def test_high_corr_flag(self):
        high = CorrelationPair("A", "B", 0.95, 20, is_high=True)
        assert high.is_high is True
