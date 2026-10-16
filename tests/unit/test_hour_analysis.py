"""시간대별 진입 성과 측정 테스트 (2026-10-16 — 개장 몰림 검증 장치)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from analytics.paper_trader import PaperTrader, _hour_bucket  # noqa: E402


class TestHourBucket:

    def test_open_burst(self):
        assert _hour_bucket("2026-10-16T09:02:33") == "09:00~09:04"
        assert _hour_bucket("2026-10-16T09:04:59") == "09:00~09:04"

    def test_regular_buckets(self):
        assert _hour_bucket("2026-10-16T09:05:00") == "09:05~09:59"
        assert _hour_bucket("2026-10-16T10:30:00") == "10:00~11:59"
        assert _hour_bucket("2026-10-16T12:00:00") == "12:00~13:59"
        assert _hour_bucket("2026-10-16T15:29:00") == "14:00~15:35"

    def test_premarket_and_bad_input(self):
        assert _hour_bucket("2026-10-16T08:50:00") == "장전"
        assert _hour_bucket("garbage") == ""
        assert _hour_bucket(None) == ""


class TestHourReport:

    def _settled(self, path, hour="09:02", n=12):
        with open(path, "w", encoding="utf-8") as f:
            for i in range(n):
                f.write(json.dumps({
                    "ts": f"2026-10-15T{hour}:{i:02d}", "date": "2026-10-15",
                    "ticker": f"{i:06d}", "horizon": 5, "win": i % 2 == 0,
                    "return_pct": 1.0 if i % 2 == 0 else -2.0,
                    "tags": [], "action": "SIGNAL_ENTRY",
                }, ensure_ascii=False) + "\n")

    def test_format_shows_open_burst(self, tmp_path):
        sp = tmp_path / "s.jsonl"
        stp = tmp_path / "t.jsonl"
        sp.write_text("", encoding="utf-8")
        self._settled(stp, "09:02", 12)   # 개장 몰림 12건
        pt = PaperTrader(db=None, signals_path=sp, settled_path=stp)
        text = pt.format_report(days=3650)
        assert "진입 시간대별" in text and "09:00~09:04" in text
        assert "n=12" in text

    def test_small_bucket_hidden(self, tmp_path):
        sp = tmp_path / "s.jsonl"
        stp = tmp_path / "t.jsonl"
        sp.write_text("", encoding="utf-8")
        self._settled(stp, "10:30", 5)   # n=5 < 10 → 숨김
        pt = PaperTrader(db=None, signals_path=sp, settled_path=stp)
        assert "진입 시간대별" not in pt.format_report(days=3650)

    def test_summary_by_hour(self, tmp_path):
        sp = tmp_path / "s.jsonl"
        stp = tmp_path / "t.jsonl"
        sp.write_text("", encoding="utf-8")
        self._settled(stp, "09:02", 12)
        pt = PaperTrader(db=None, signals_path=sp, settled_path=stp)
        s = pt.summary(days=3650)
        assert s["by_hour"]["09:00~09:04"]["n"] == 12
        assert s["by_hour"]["09:00~09:04"]["win_rate"] == 50.0
