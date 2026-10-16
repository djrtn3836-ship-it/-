"""신호 유형 라벨 + 알림 선별 필터 테스트 (2026-10-16 Session 115).

사용자 질문: "단타/스윙/장기 구분 로직이 없냐" → 유형 잠정 라벨 도입
+ 데이터 근거 필터(① 하위 신뢰도 알림 제외 ② 안전가드 차단 중 보류).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.bootstrap import Bootstrapper  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]


class TestTradeType:

    def _cls(self, details, action="BUY"):
        return Bootstrapper._classify_trade_type(
            {"action": action, "strategy_scores": {"scores": {"details": details}}}
        )

    def test_trend_dominant_is_swing(self):
        assert self._cls(["Trend: BUY (78%)", "Reversal: HOLD (50%)", "Breakout: BUY (72%)"]) == "스윙(추정)"

    def test_reversal_dominant_is_scalp(self):
        assert self._cls(["Reversal: BUY (65%)", "Trend: HOLD (40%)", "Breakout: HOLD (35%)"]) == "단타(추정)"

    def test_side_must_match(self):
        # Reversal이 더 높아도 액션이 HOLD면 후보 아님 → Trend(스윙) 선택
        assert self._cls(["Trend: BUY (55%)", "Reversal: HOLD (90%)"]) == "스윙(추정)"

    def test_empty_details_no_label(self):
        assert self._cls([]) == ""
        assert Bootstrapper._classify_trade_type({}) == ""

    def test_bad_format_is_safe(self):
        assert self._cls(["콜론 없는 문자열", "Reversal: BUY (abc%)"]) == ""


class TestAlertFilters:

    def test_min_conf_default_and_env(self, monkeypatch):
        import os

        monkeypatch.delenv("SIGNAL_MIN_CONFIDENCE", raising=False)
        assert Bootstrapper._sig_min_conf() == 0.60
        monkeypatch.setenv("SIGNAL_MIN_CONFIDENCE", "0.55")
        assert Bootstrapper._sig_min_conf() == 0.55

    def test_gates_wired(self):
        src = (_ROOT / "app" / "bootstrap.py").read_text(encoding="utf-8")
        assert "_low_conf" in src and "_sig_min_conf()" in src
        assert "_safety_blocked" in src, "안전가드 차단 중 보류가 없음"
        i = src.index("_suppressed = (")
        seg = src[i : i + 400]
        assert "_low_conf" in seg and "_blocked" in seg

    def test_type_attached_and_passed(self):
        src = (_ROOT / "app" / "bootstrap.py").read_text(encoding="utf-8")
        assert 'analysis["trade_type"] = self._classify_trade_type(analysis)' in src
        assert "trade_type=str(analysis.get(\"trade_type\") or \"\")" in src

    def test_sender_renders_type(self):
        from report.telegram_sender import TelegramSender

        s = TelegramSender.__new__(TelegramSender)
        s.bot = object()
        s.chat_id = "1"
        text = s.format({"action": "SIGNAL_ENTRY", "ticker": "005930", "side": "BUY", "trade_type": "스윙(추정)"})
        assert "신호 유형" in text and "스윙(추정)" in text

    def test_paper_records_type(self):
        src = (_ROOT / "analytics" / "paper_trader.py").read_text(encoding="utf-8")
        assert '"trade_type": str(trade_type or ""),' in src
        assert "trade_type: str = \"\"," in src
