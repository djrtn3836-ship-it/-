"""종목명 표시·장외 알림 차단 테스트 (2026-10-12 Session 110 — 사용자 피드백).

① 신호가 숫자 코드로만 표시 → "삼성전자(005930)"로 개선
② 장외(시간외단일가 ~18:00) 신호 알림 차단(기록·모의매매는 유지)
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.stock_names import stock_name  # noqa: E402


def _write_csv(tmp_path, rows):
    p = tmp_path / "uni.csv"
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "name", "market"])
        w.writerows(rows)
    return p


class TestStockNames:

    def test_resolves_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "STOCK_NAMES_CSV", str(_write_csv(tmp_path, [["005930", "삼성전자", "KOSPI"]]))
        )
        assert stock_name("005930") == "삼성전자"

    def test_unknown_returns_input(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "STOCK_NAMES_CSV", str(_write_csv(tmp_path, [["005930", "삼성전자", "KOSPI"]]))
        )
        assert stock_name("999999") == "999999"

    def test_missing_csv_is_harmless(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STOCK_NAMES_CSV", str(tmp_path / "none.csv"))
        assert stock_name("005930") == "005930"

    def test_mtime_cache_reload(self, tmp_path, monkeypatch):
        p = _write_csv(tmp_path, [["005930", "삼성전자", "KOSPI"]])
        monkeypatch.setenv("STOCK_NAMES_CSV", str(p))
        assert stock_name("000660") == "000660"
        import os
        import time

        time.sleep(0.05)
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "name", "market"])
            w.writerow(["000660", "SK하이닉스", "KOSPI"])
        os.utime(p, (time.time() + 1, time.time() + 1))
        assert stock_name("000660") == "SK하이닉스", "CSV 갱신이 반영되지 않음"

    def test_real_universe_csv(self):
        root = Path(__file__).resolve().parents[2]
        if not (root / "data" / "krx_universe.csv").exists():
            return
        assert stock_name("005930") == "삼성전자"


class TestTelegramNameInjection:

    def test_format_injects_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "STOCK_NAMES_CSV", str(_write_csv(tmp_path, [["005930", "삼성전자", "KOSPI"]]))
        )
        from report.telegram_sender import TelegramSender

        s = TelegramSender.__new__(TelegramSender)
        s.bot = object()
        s.chat_id = "1"
        text = s.format({"action": "SIGNAL_ENTRY", "ticker": "005930", "side": "BUY"})
        assert text and "삼성전자" in text and "005930" in text

    def test_format_raw_untouched(self):
        from report.telegram_sender import TelegramSender

        s = TelegramSender.__new__(TelegramSender)
        s.bot = object()
        s.chat_id = "1"
        assert s.format({"action": "RAW", "text": "관리 알림"}) == "관리 알림"


class TestOffHoursGate:

    def test_gate_uses_market_open(self, monkeypatch):
        from app.bootstrap import Bootstrapper
        import core.holiday_utils as hu

        monkeypatch.setattr(hu, "is_market_open", lambda: False)
        assert Bootstrapper._alert_hours_ok() is False
        monkeypatch.setattr(hu, "is_market_open", lambda: True)
        assert Bootstrapper._alert_hours_ok() is True

    def test_gate_wired_in_alert_block(self):
        src = (
            Path(__file__).resolve().parents[2] / "app" / "bootstrap.py"
        ).read_text(encoding="utf-8")
        assert "_alert_hours_ok()" in src
        i = src.index("_suppressed = (not self._alert_hours_ok())")
        j = src.index("self.paper_trader.record_signal", i)
        assert j > i, "장외 차단이 모의매매 기록보다 앞에 있어야 한다(기록 유지)"
