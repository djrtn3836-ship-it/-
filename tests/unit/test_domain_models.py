"""
tests/unit/test_domain_models.py - V10 Domain Models 단위 테스트
- Signal, Action, Decision 불변 도메인 모델 검증
- StrategyResult 및 Strategy ABC 검증
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from domain.models.signal import Action, Signal, Decision
from domain.strategies.base import Strategy, StrategyResult


class TestAction:
    """Action enum 테스트"""

    def test_from_str_buy(self):
        assert Action.from_str("buy") == Action.BUY
        assert Action.from_str("BUY") == Action.BUY

    def test_from_str_unknown_defaults_hold(self):
        assert Action.from_str("UNKNOWN") == Action.HOLD

    def test_is_trade(self):
        assert Action.BUY.is_trade is True
        assert Action.SELL.is_trade is True
        assert Action.HOLD.is_trade is False
        assert Action.ERROR.is_trade is False

    def test_label(self):
        assert Action.BUY.label == "Buy"
        assert Action.HOLD.label == "Hold"


class TestSignal:
    """Signal 불변 도메인 모델 테스트"""

    def _make_signal(self, **kwargs) -> Signal:
        defaults = dict(
            ticker="005930",
            action=Action.BUY,
            score=0.75,
            confidence=0.8,
            price=83000.0,
        )
        defaults.update(kwargs)
        return Signal(**defaults)

    def test_create_valid_signal(self):
        s = self._make_signal()
        assert s.ticker == "005930"
        assert s.action == Action.BUY
        assert s.score == 0.75
        assert s.is_trade is True

    def test_immutable(self):
        """frozen=True 불변성 검증"""
        s = self._make_signal()
        with pytest.raises((AttributeError, TypeError)):
            s.score = 0.9  # type: ignore

    def test_invalid_ticker_raises(self):
        with pytest.raises(ValueError):
            self._make_signal(ticker="12345")  # 6자리 아님

    def test_invalid_score_raises(self):
        with pytest.raises(ValueError):
            self._make_signal(score=1.5)  # 범위 초과

    def test_invalid_price_raises(self):
        with pytest.raises(ValueError):
            self._make_signal(price=0.0)  # 0 이하

    def test_error_signal_factory(self):
        s = Signal.error("005930", "Test error")
        assert s.action == Action.ERROR
        assert "Test error" in s.negatives
        assert s.score == 0.0

    def test_to_dict(self):
        s = self._make_signal(positives=["Strong momentum"])
        d = s.to_dict()
        assert d["ticker"] == "005930"
        assert d["action"] == "BUY"
        assert d["positives"] == ["Strong momentum"]

    def test_action_label(self):
        s = self._make_signal(action=Action.SELL)
        assert s.action_label == "Sell"


class TestDecision:
    """Decision 불변 도메인 모델 테스트"""

    def _make_decision(self, **kwargs) -> Decision:
        signal = Signal(
            ticker="005930", action=Action.BUY,
            score=0.75, confidence=0.8, price=83000.0,
        )
        defaults = dict(signal=signal, risk_adjusted_score=0.7)
        defaults.update(kwargs)
        return Decision(**defaults)

    def test_create_valid_decision(self):
        d = self._make_decision(stop_loss=80000.0, take_profit_1=86000.0)
        assert d.ticker == "005930"
        assert d.action == Action.BUY
        assert d.is_trade is True

    def test_risk_reward_ratio_buy(self):
        """BUY: (TP1 - price) / (price - SL)"""
        d = self._make_decision(stop_loss=80000.0, take_profit_1=89000.0)
        # (89000 - 83000) / (83000 - 80000) = 6000 / 3000 = 2.0
        assert abs(d.risk_reward_ratio - 2.0) < 0.01

    def test_invalid_risk_score_raises(self):
        with pytest.raises(ValueError):
            self._make_decision(risk_adjusted_score=1.5)

    def test_to_dict(self):
        d = self._make_decision()
        result = d.to_dict()
        assert "ticker" in result
        assert "risk_adjusted_score" in result
        assert "risk_reward_ratio" in result


class TestStrategyResult:
    """StrategyResult 테스트"""

    def test_is_trade(self):
        r = StrategyResult(name="test", action="BUY", score=0.8, confidence=0.9)
        assert r.is_trade is True
        r2 = StrategyResult(name="test", action="HOLD", score=0.5, confidence=0.6)
        assert r2.is_trade is False

    def test_to_dict(self):
        r = StrategyResult(
            name="trend", action="BUY", score=0.7, confidence=0.8,
            reasons=["MA cross"], metadata={"regime": "Bull"}
        )
        d = r.to_dict()
        assert d["name"] == "trend"
        assert d["reasons"] == ["MA cross"]
        assert d["metadata"]["regime"] == "Bull"


class TestStrategyABC:
    """Strategy ABC 인터페이스 검증"""

    def test_abstract_cannot_instantiate(self):
        """ABC는 직접 인스턴스화 불가"""
        with pytest.raises(TypeError):
            Strategy()  # type: ignore

    def test_concrete_strategy(self):
        """구체 전략 구현 검증"""
        from domain.strategies.trend import TrendStrategy

        s = TrendStrategy()
        assert s.name == "Trend"
        assert 0 < s.weight <= 1.0

    def test_strategy_repr(self):
        from domain.strategies.trend import TrendStrategy
        s = TrendStrategy()
        assert "TrendStrategy" in repr(s)
