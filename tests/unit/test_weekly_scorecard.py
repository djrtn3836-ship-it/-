# -*- coding: utf-8 -*-
"""주간 성적표(S136 ⑤ 자동화) 테스트."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scheduler.weekly_scorecard as ws  # noqa: E402


def _patch_ok(monkeypatch, rc_sig=0, rc_ml=1):
    import validation.ml_discrimination_test as ml
    import validation.signal_vs_random_test as sig

    monkeypatch.setattr(sig, "main", lambda argv=None: rc_sig)
    monkeypatch.setattr(ml, "main", lambda argv=None: rc_ml)


class TestScorecard:

    def test_writes_file_and_rc(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ws, "LOG_DIR", tmp_path)
        _patch_ok(monkeypatch)
        out = ws.run_scorecard()
        assert out["rc"] == {"signal_vs_random": 0, "ml_discrimination": 1}
        p = Path(out["path"])
        assert p.exists() and "주간 성적표" in p.read_text(encoding="utf-8")

    def test_tool_error_isolated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ws, "LOG_DIR", tmp_path)
        import validation.ml_discrimination_test as ml
        import validation.signal_vs_random_test as sig

        def _boom(argv=None):
            raise RuntimeError("도구 오류")

        monkeypatch.setattr(sig, "main", _boom)
        monkeypatch.setattr(ml, "main", lambda argv=None: 0)
        out = ws.run_scorecard()
        assert out["rc"]["signal_vs_random"] == -1, "도구 오류는 격리(-1)"
        assert out["rc"]["ml_discrimination"] == 0, "다른 도구는 정상 실행"
