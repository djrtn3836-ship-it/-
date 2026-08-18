#!/usr/bin/env python3
"""
run_integration_tests.py - 통합 테스트 실행기 v3.3 (UTF-8 강제 + ASCII 태그)
- 각 테스트를 별도 프로세스로 실행하며, PYTHONIOENCODING=utf-8 강제 설정
- 실패 시 전체 traceback을 캡처하여 출력
"""

import os
import sys
import subprocess
import time
from pathlib import Path
from datetime import datetime
from typing import List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent
TEST_DIR = PROJECT_ROOT / "tests"
EXCLUDE_PATTERNS = [
    "run_integration_tests.py",
    "run_all_tests.py",
    "conftest.py",
    "__pycache__",
]
TIMEOUT_SECONDS = 300

def is_test_file(file_path: Path) -> bool:
    if file_path.suffix != ".py":
        return False
    name = file_path.name
    for pat in EXCLUDE_PATTERNS:
        if pat in name:
            return False
    return True

def find_test_files() -> List[Path]:
    if not TEST_DIR.exists():
        print(f"[WARN] tests/ 폴더가 없습니다. 생성합니다...")
        TEST_DIR.mkdir(parents=True, exist_ok=True)
        return []
    test_files = []
    for file_path in TEST_DIR.rglob("*.py"):
        if is_test_file(file_path):
            test_files.append(file_path)
    return sorted(set(test_files))

def run_test(file_path: Path) -> Tuple[bool, str, str]:
    """단일 테스트 실행 (UTF-8 강제)"""
    cmd = [sys.executable, str(file_path)]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    # 🔥 UTF-8 강제 (cp949 오류 해결)
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            encoding='utf-8',
            errors='replace',
            env=env
        )
        stdout = result.stdout
        stderr = result.stderr
        success = (result.returncode == 0)
        return success, stdout, stderr
    except subprocess.TimeoutExpired:
        return False, "", f"[TIMEOUT] 시간 초과 ({TIMEOUT_SECONDS}초)"
    except Exception as e:
        return False, "", f"[ERROR] 실행 오류: {e}"

def main():
    # 콘솔 UTF-8 재설정 (메인 프로세스 자체도 보호)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    print("\n" + "=" * 70)
    print("[TEST-RUNNER] 통합 테스트 실행기 v3.3 (UTF-8 강제)")
    print(f"   프로젝트 루트: {PROJECT_ROOT}")
    print(f"   테스트 폴더: {TEST_DIR}")
    print(f"   시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    test_files = find_test_files()
    if not test_files:
        print("[FAIL] tests/ 폴더에 테스트 파일이 없습니다.")
        sys.exit(1)

    print(f"\n[INFO] 발견된 테스트 파일: {len(test_files)}개")
    for f in test_files:
        rel = f.relative_to(PROJECT_ROOT)
        print(f"   • {rel}")

    results = {}
    total = len(test_files)
    passed = 0
    failed = 0

    print("\n" + "-" * 70)
    print("[INFO] 테스트 실행 시작...")
    start_time = time.time()

    for idx, file_path in enumerate(test_files, 1):
        rel_path = file_path.relative_to(PROJECT_ROOT)
        print(f"\n[{idx}/{total}] 실행: {rel_path}")
        success, stdout, stderr = run_test(file_path)

        results[rel_path] = {
            "success": success,
            "stdout": stdout[:1000] + ("..." if len(stdout) > 1000 else ""),
            "stderr": stderr[:1000] + ("..." if len(stderr) > 1000 else ""),
        }

        if success:
            print(f"   [PASS] 성공")
            passed += 1
        else:
            print(f"   [FAIL] 실패")
            if stderr:
                print(f"   [DETAIL] 오류 상세:\n{stderr[:800]}")
            failed += 1

    elapsed = time.time() - start_time

    print("\n" + "=" * 70)
    print("[RESULT] 통합 테스트 최종 결과")
    print(f"   총 테스트: {total}개")
    print(f"   [PASS] 통과: {passed}개")
    print(f"   [FAIL] 실패: {failed}개")
    print(f"   소요 시간: {elapsed:.1f}초")

    if failed == 0:
        print("\n [SUCCESS] 모든 테스트를 통과했습니다!")
    else:
        print("\n [FAIL] 일부 테스트가 실패했습니다.")
        print("   실패한 테스트:")
        for rel_path, info in results.items():
            if not info["success"]:
                print(f"      [FAIL] {rel_path}")

    print("=" * 70)

    # 보고서 저장
    report_path = PROJECT_ROOT / "logs" / f"integration_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "total": total,
                "passed": passed,
                "failed": failed,
                "results": {str(k): v for k, v in results.items()}
            }, f, indent=2, ensure_ascii=False)
        print(f"\n[INFO] 상세 보고서 저장됨: {report_path}")
    except Exception as e:
        print(f"[WARN] 보고서 저장 실패: {e}")

    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()