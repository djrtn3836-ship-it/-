#!/usr/bin/env python3
"""
통합 검증 실행기 (Validation Suite Runner) - 루트 버전
- 프로젝트 루트에서 직접 실행 (모든 테스트 파일이 루트에 있음)
"""

import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent

TEST_SCRIPTS = [
    "diagnose_system.py",
    "scan_all_files.py",
    "test_domestic_mock.py",
    "test_naver_api.py",
    "test_naver_simple.py",
    "test_parser.py",
    "test_telegram_events.py",
]

def run_test(script_name: str) -> bool:
    script_path = PROJECT_ROOT / script_name
    if not script_path.exists():
        print(f"❌ {script_name} 없음")
        return False

    print(f"\n{'='*60}")
    print(f"▶ 실행: {script_name}")
    print(f"   시작: {datetime.now().strftime('%H:%M:%S')}")
    print('='*60)

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=False,  # 실시간 출력을 위해 False
            text=True,
            timeout=300,
            encoding='utf-8'
        )
        if result.returncode == 0:
            print(f"✅ {script_name} 성공")
            return True
        else:
            print(f"❌ {script_name} 실패 (코드: {result.returncode})")
            return False
    except Exception as e:
        print(f"💥 {script_name} 오류: {e}")
        return False

def main():
    print("\n" + "="*70)
    print("🧪 [통합 검증 실행기] (루트 버전)")
    print(f"   프로젝트: {PROJECT_ROOT}")
    print(f"   시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)

    os.chdir(PROJECT_ROOT)

    results = {}
    passed = 0
    for script in TEST_SCRIPTS:
        ok = run_test(script)
        results[script] = ok
        if ok:
            passed += 1

    print("\n" + "="*70)
    print("🏁 [최종 결과]")
    print(f"   총 {len(TEST_SCRIPTS)}개 | ✅ {passed}개 통과 | ❌ {len(TEST_SCRIPTS)-passed}개 실패")
    for name, ok in results.items():
        print(f"      {'✅' if ok else '❌'} {name}")
    print("="*70)

    if passed < len(TEST_SCRIPTS):
        sys.exit(1)

if __name__ == "__main__":
    main()