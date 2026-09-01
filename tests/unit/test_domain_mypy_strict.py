# -*- coding: utf-8 -*-
"""tests/unit/test_domain_mypy_strict.py - domain 모델 mypy strict 준수 확인

⚠️ 이번 세션에서 실제로 검증·수정한 domain/models/signal.py,
domain/strategies/base.py 2개 파일만 대상으로 합니다. 다른 모듈까지 통과를
단언하지 않습니다 (미검증 상태에서의 과신을 방지).
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _run_mypy_strict(target: str) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "mypy", "--strict", "--ignore-missing-imports",
           str(PROJECT_ROOT / target)]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    return result.returncode, result.stdout + result.stderr


class TestDomainMypyStrict:

    def test_signal_py_strict(self):
        code, output = _run_mypy_strict("domain/models/signal.py")
        assert code == 0, f"mypy strict 실패:\n{output}"

    def test_strategy_base_py_strict(self):
        code, output = _run_mypy_strict("domain/strategies/base.py")
        assert code == 0, f"mypy strict 실패:\n{output}"
