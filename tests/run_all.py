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

SKIP_DIRS = {"venv", "__pycache__", ".git", "fonts", "_archive", "logs", "reports"}


def check_sources() -> int:
    """소스 정적 검사: BOM / 구문 오류 / 뭉개짐 / await 누락.

    ⚠️ 2026-10-01 사고: 편집 도구가 문자열 리터럴을 깨뜨려 telegram_commands.py가
    SyntaxError 상태가 되었는데도 테스트는 통과했다(해당 모듈을 import하는 테스트가 없음).
    소스 검사가 CI에만 있어 로컬에서 놓쳤다 → run_all.py에 편입.
    """
    import ast
    import os

    bad: list[tuple[str, int, str]] = []
    bom: list[str] = []
    flat: list[str] = []
    unawaited: list[str] = []
    n = 0

    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = Path(root) / fn
            rel = str(path.relative_to(ROOT))
            n += 1
            raw = path.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                bom.append(rel)
            if len(raw) > 200 and raw.count(b"\n") == 0:
                flat.append(rel)
                continue
            try:
                tree = ast.parse(raw.decode("utf-8"))
            except SyntaxError as e:
                bad.append((rel, e.lineno or 0, e.msg))
                continue
            if len(raw) > 200 and not tree.body:
                flat.append(rel)
                continue
            if rel.startswith("tests"):
                continue
            for cls in [c for c in ast.walk(tree) if isinstance(c, ast.ClassDef)]:
                async_methods = {m.name for m in cls.body if isinstance(m, ast.AsyncFunctionDef)}
                for node in ast.walk(cls):
                    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                        f = node.value.func
                        if (
                            isinstance(f, ast.Attribute)
                            and isinstance(f.value, ast.Name)
                            and f.value.id == "self"
                            and f.attr in async_methods
                        ):
                            unawaited.append(f"{rel}:{node.lineno} self.{f.attr}()")

    problems = len(bad) + len(bom) + len(flat) + len(unawaited)
    print(
        f"\n▶ 소스 검사: py={n} BOM={len(bom)} syntax={len(bad)} "
        f"flattened={len(flat)} unawaited={len(unawaited)}",
        flush=True,
    )
    for rel, line, msg in bad:
        print(f"  🔴 syntax: {rel}:{line} {msg}", flush=True)
    for rel in bom:
        print(f"  🔴 BOM: {rel}", flush=True)
    for rel in flat:
        print(f"  🔴 flattened: {rel}", flush=True)
    for item in unawaited:
        print(f"  🔴 unawaited: {item}", flush=True)
    return 1 if problems else 0


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

    source_code = check_sources()
    if source_code != 0:
        print("\n❌ 소스 정적 검사 실패 — 테스트를 실행하지 않습니다.", flush=True)
        return source_code

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
