# -*- coding: utf-8 -*-
"""validation/ml_discrimination_test.py 테스트 (S135)."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from validation.ml_discrimination_test import (  # noqa: E402
    add_excess,
    filter_rows,
    main,
    quantile_bins,
    spearman,
    summarize,
    welch_t,
)


def _row(conf=0.65, ml=0.6, score=0.45, r1=1.0, r5=2.0, ok=True, action="BUY", date="2026-10-12"):
    return {
        "id": 1, "ticker": "A", "action": action, "confidence": conf, "ml_score": ml,
        "score": score, "date": date, "return_1d": r1, "return_5d": r5, "is_correct": ok,
    }


class TestFilter:

    def test_action_and_artifact(self):
        rows = [
            _row(),                             # 유지
            _row(action="HOLD"),                # 행동 필터
            _row(r1=99.0),                      # 1d 아티팩트
            _row(r1=1.0, r5=400.0),             # 5d 아티팩트
            _row(r1=None),                      # 키 없음
        ]
        kept, dropped = filter_rows(rows, ("BUY", "SIGNAL_ENTRY"))
        assert len(kept) == 1
        assert dropped == {"artifact_1d": 1, "artifact_5d": 1, "no_key": 1}


class TestExcess:

    def test_demean_same_day(self):
        rows = [_row(r1=2.0), _row(r1=4.0)]
        add_excess(rows)
        assert abs(float(rows[0]["ex1d"]) + 1.0) < 1e-9
        assert abs(float(rows[1]["ex1d"]) - 1.0) < 1e-9

    def test_different_days_not_mixed(self):
        rows = [_row(r1=2.0, date="2026-10-12"), _row(r1=8.0, date="2026-10-13")]
        add_excess(rows)
        assert abs(float(rows[0]["ex1d"])) < 1e-9, "각 날짜 평균이 자기 자신"


class TestBins:

    def test_quantile_split(self):
        rows = [_row(conf=0.60 + i * 0.01) for i in range(10)]
        bins = quantile_bins(rows, "confidence", n_bins=5)
        assert len(bins) == 5 and all(len(b[1]) == 2 for b in bins)
        assert bins[0][2] == 0.60 and bins[-1][3] == 0.69

    def test_spearman_monotone(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert abs(spearman(xs, [1.0, 2.0, 3.0, 4.0, 5.0]) - 1.0) < 1e-9
        assert abs(spearman(xs, [5.0, 4.0, 3.0, 2.0, 1.0]) + 1.0) < 1e-9

    def test_welch(self):
        assert welch_t([1.0, 1.0, 1.0], [1.0, 1.0, 1.0]) == 0.0
        assert welch_t([3.0, 3.1, 2.9], [0.0, 0.1, -0.1]) > 5

    def test_summarize_hit(self):
        rows = [_row(ok=True), _row(ok=False)]
        add_excess(rows)
        st = summarize(rows)
        assert st["n"] == 2 and st["hit"] == 50.0


class TestMain:

    def _db(self, tmp_path):
        db = tmp_path / "t.db"
        con = sqlite3.connect(db)
        con.execute("""CREATE TABLE decisions (id INTEGER PRIMARY KEY, ticker TEXT, action TEXT,
            score REAL, confidence REAL, price_at_decision REAL, created_at TEXT, ml_score REAL)""")
        con.execute("""CREATE TABLE decision_outcomes (decision_id INTEGER, return_1d REAL,
            return_5d REAL, is_correct INTEGER)""")
        for i in range(20):
            con.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)",
                        (i, "A", "BUY", 0.45, 0.62 + i * 0.004, 100.0, "2026-10-12 05:00:00", 0.3 + i * 0.02))
            con.execute("INSERT INTO decision_outcomes VALUES (?,?,?,?)",
                        (i, 0.1 * i, 0.2 * i, 1 if i % 2 else 0))
        con.commit()
        con.close()
        return db

    def test_main_runs(self, tmp_path, capsys):
        rc = main(["--db", str(self._db(tmp_path))])
        out = capsys.readouterr().out
        assert rc == 0
        assert "판별력 검증" in out and "Spearman" in out and "ml_score" in out

    def test_main_empty_db(self, tmp_path, capsys):
        db = tmp_path / "e.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE decisions (id INTEGER, ticker TEXT, action TEXT, score REAL, confidence REAL, price_at_decision REAL, created_at TEXT, ml_score REAL)")
        con.execute("CREATE TABLE decision_outcomes (decision_id INTEGER, return_1d REAL, return_5d REAL, is_correct INTEGER)")
        con.commit()
        con.close()
        assert main(["--db", str(db)]) == 1
        assert "확정 outcome 없음" in capsys.readouterr().out
