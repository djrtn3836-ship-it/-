# -*- coding: utf-8 -*-
"""tests/run_all.py - 통합 테스트 실행기 (단일 명령).

사용:
    python tests/run_all.py              # 기본: unit + integration (performance 제외)
    python tests/run_all.py --all        # performance 포함 전체
    python tests/run_all.py --unit       # unit만
    python tests/run_all.py --quick      # 실패 시 즉시 중단(-x)

성격:
    - pytest가 없거나 pyproject 손상 등으로 수집이 실패하면 원인을 그대로 보여준다.
    - 종료 코드: 0=성공, 1=테스트 실패, 2=수집/설정 오류
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(args: list[str]) -> int:
    cmd = [sys.executable, "-m", "pytest", *args, "--no-header", "-q"]
    print(f"\n▶ {' '.join(cmd)}\n", flush=True)
    proc = subprocess.run(cmd, cwd=str(ROOT))
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="통합 테스트 실행기")
    parser.add_argument("--all", action="store_true", help="performance 포함 전체")
    parser.add_argument("--unit", action="store_true", help="unit 테스트만")
    parser.add_argument("--quick", action="store_true", help="첫 실패에서 중단(-x)")
    opts = parser.parse_args(argv)

    if opts.unit:
        targets = ["tests/unit"]
    elif opts.all:
        targets = ["tests"]
    else:
        targets = ["tests", "--ignore=tests/performance"]

    if opts.quick:
        targets.append("-x")

    code = _run(targets)
    if code == 0:
        print("\n✅ 모든 테스트 통과", flush=True)
    elif code == 5:
        print("\n⚠️ 수집된 테스트가 없습니다 (설정 확인)", flush=True)
        return 2
    else:
        print(f"\n❌ 테스트 실패 (exit={code})", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
