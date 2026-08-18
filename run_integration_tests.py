#!/usr/bin/env python3
"""
run_integration_tests.py - 통합 테스트 실행기 v3.1 (PYTHONPATH 자동 설정)
- 프로젝트 루트의 run_integration_tests.py를 실행하면 tests/ 폴더의 모든 테스트를 자동 탐색
- 각 테스트는 프로젝트 루트를 현재 작업 디렉토리(cwd)로 설정하고, PYTHONPATH에 프로젝트 루트를 추가하여 실행
- 따라서 테스트 파일이 tests/로 이동해도 import 경로 문제 없음
- 결과를 종합하여 최종 리포트 출력
"""

import os
import sys
import subprocess
import time
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple

# ============================================================
# 설정
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent
TEST_DIR = PROJECT_ROOT / "tests"          # 모든 테스트는 이 폴더에 있음
EXCLUDE_PATTERNS = [
    "run_integration_tests.py",
    "run_all_tests.py",
    "conftest.py",
    "__pycache__",
]
TIMEOUT_SECONDS = 300

# ============================================================
# 유틸리티 함수
# ============================================================
def is_test_file(file_path: Path) -> bool:
    """tests/ 폴더 내의 테스트 파일인지 판별"""
    if file_path.suffix != ".py":
        return False
    name = file_path.name
    for pat in EXCLUDE_PATTERNS:
        if pat in name:
            return False
    return True

def find_test_files() -> List[Path]:
    """tests/ 폴더에서 모든 테스트 파일을 탐색"""
    if not TEST_DIR.exists():
        print(f"⚠️ tests/ 폴더가 없습니다. 생성합니다...")
        TEST_DIR.mkdir(parents=True, exist_ok=True)
        return []
    test_files = []
    for file_path in TEST_DIR.rglob("*.py"):
        if is_test_file(file_path):
            test_files.append(file_path)
    return sorted(set(test_files))

def run_test(file_path: Path) -> Tuple[bool, str, str]:
    """단일 테스트 실행 (루트를 cwd로, PYTHONPATH 설정)"""
    cmd = [sys.executable, str(file_path)]
    # 🔥 PYTHONPATH 설정: 프로젝트 루트를 추가
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    if pythonpath:
        env["PYTHONPATH"] = f"{PROJECT_ROOT}{os.pathsep}{pythonpath}"
    else:
        env["PYTHONPATH"] = str(PROJECT_ROOT)
    
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            encoding='utf-8',
            errors='replace',
            env=env   # 🔥 환경 변수 전달
        )
        stdout = result.stdout
        stderr = result.stderr
        success = (result.returncode == 0)
        return success, stdout, stderr
    except subprocess.TimeoutExpired:
        return False, "", f"⏰ 시간 초과 ({TIMEOUT_SECONDS}초)"
    except Exception as e:
        return False, "", f"💥 실행 오류: {e}"

# ============================================================
# 메인 실행
# ============================================================
def main():
    print("\n" + "=" * 70)
    print("🧪 [통합 테스트 실행기 v3.1] (PYTHONPATH 자동 설정)")
    print(f"   프로젝트 루트: {PROJECT_ROOT}")
    print(f"   테스트 폴더: {TEST_DIR}")
    print(f"   시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 1. 테스트 파일 탐색
    test_files = find_test_files()
    if not test_files:
        print("❌ tests/ 폴더에 테스트 파일이 없습니다.")
        print("   테스트 파일을 tests/ 폴더로 이동한 후 다시 실행하세요.")
        sys.exit(1)

    print(f"\n📂 발견된 테스트 파일: {len(test_files)}개")
    for f in test_files:
        rel = f.relative_to(PROJECT_ROOT)
        print(f"   • {rel}")

    # 2. 순차 실행
    results = {}
    total = len(test_files)
    passed = 0
    failed = 0

    print("\n" + "-" * 70)
    print("🚀 테스트 실행 시작...")
    start_time = time.time()

    for idx, file_path in enumerate(test_files, 1):
        rel_path = file_path.relative_to(PROJECT_ROOT)
        print(f"\n[{idx}/{total}] 실행: {rel_path}")
        success, stdout, stderr = run_test(file_path)

        results[rel_path] = {
            "success": success,
            "stdout": stdout[:500] + ("..." if len(stdout) > 500 else ""),
            "stderr": stderr[:500] + ("..." if len(stderr) > 500 else ""),
        }

        if success:
            print(f"   ✅ 성공")
            passed += 1
        else:
            print(f"   ❌ 실패 (종료 코드: {1 if success is False else '?'})")
            if stderr:
                print(f"   ⚠️ 오류 메시지: {stderr[:200]}")
            failed += 1

    elapsed = time.time() - start_time

    # 3. 최종 요약
    print("\n" + "=" * 70)
    print("🏁 [통합 테스트 최종 결과]")
    print(f"   총 테스트: {total}개")
    print(f"   ✅ 통과: {passed}개")
    print(f"   ❌ 실패: {failed}개")
    print(f"   ⏱️ 소요 시간: {elapsed:.1f}초")

    if failed == 0:
        print("\n 🎉 모든 테스트를 통과했습니다! 시스템이 건강합니다.")
    else:
        print("\n 🚨 일부 테스트가 실패했습니다. 위 로그를 확인하여 수정하세요.")
        print("   실패한 테스트:")
        for rel_path, info in results.items():
            if not info["success"]:
                print(f"      ❌ {rel_path}")

    print("=" * 70)

    # 상세 결과를 JSON 파일로 저장
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
        print(f"\n📄 상세 보고서 저장됨: {report_path}")
    except Exception as e:
        print(f"⚠️ 보고서 저장 실패: {e}")

    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    main()