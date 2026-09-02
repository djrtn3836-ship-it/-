# -*- coding: utf-8 -*-
"""tests/unit/test_domain_mypy_strict.py - domain 모듈 mypy strict 준수 확인 (5개)

Session 20: signal.py, base.py
Session 21: trend.py, reversal.py, breakout.py 추가
실제 검증·수정 완료된 파일만 포함합니다.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _run_mypy_strict(target: str) -> tuple[int, str]:
    cmd = [
        sys.executable, "-m", "mypy", "--strict",
        "--ignore-missing-imports",
        str(PROJECT_ROOT / target),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    return result.returncode, result.stdout + result.stderr


class TestDomainMypyStrict:

    def test_signal_py_strict(self) -> None:
        code, output = _run_mypy_strict("domain/models/signal.py")
        assert code == 0, f"mypy strict 실패:\n{output}"

    def test_strategy_base_py_strict(self) -> None:
        code, output = _run_mypy_strict("domain/strategies/base.py")
        assert code == 0, f"mypy strict 실패:\n{output}"

    def test_trend_strategy_strict(self) -> None:
        code, output = _run_mypy_strict("domain/strategies/trend.py")
        assert code == 0, f"mypy strict 실패:\n{output}"

    def test_reversal_strategy_strict(self) -> None:
        code, output = _run_mypy_strict("domain/strategies/reversal.py")
        assert code == 0, f"mypy strict 실패:\n{output}"

    def test_breakout_strategy_strict(self) -> None:
        code, output = _run_mypy_strict("domain/strategies/breakout.py")
        assert code == 0, f"mypy strict 실패:\n{output}"
