#!/usr/bin/env python3
"""
🔬 PROJECT-WIDE FILE SCANNER v2.0 (완전 전수 검증기)
- 기존: 문법(Syntax) + 임포트(Import) 검사
- 🔥 신규: pyflakes 연동 (미사용 임포트, 정의되지 않은 이름(NameError) 검출)
- 사용법: python scan_all_files.py
"""
import os
import sys
import ast
import importlib
import traceback
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple

# 🔥 pyflakes 임포트 시도 (선택적)
try:
    import pyflakes.api
    import pyflakes.reporter
    HAS_PYFLAKES = True
except ImportError:
    HAS_PYFLAKES = False

# ============================================================
# 색상 및 프린트 함수 (Windows CMD 호환)
# ============================================================
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_ok(msg): print(f" {Colors.GREEN}✅{Colors.END} {msg}")
def print_fail(msg): print(f" {Colors.RED}❌{Colors.END} {msg}")
def print_warn(msg): print(f" {Colors.YELLOW}⚠️{Colors.END} {msg}")
def print_info(msg): print(f" {Colors.BLUE}ℹ️{Colors.END} {msg}")
def print_title(msg): print(f"\n{Colors.BOLD}🔍 {msg}{Colors.END}")

class ProjectFileScanner:
    def __init__(self):
        self.root_dir = Path(__file__).parent
        self.results = {
            "total_files": 0,
            "passed": 0,
            "syntax_errors": 0,
            "import_errors": 0,
            "pyflakes_errors": 0,
            "other_errors": 0,
            "details": []
        }
        self.exclude_dirs = {
            '.git', '__pycache__', 'venv', 'env', 
            'logs', 'reports', 'fonts', '.pytest_cache'
        }

    def should_scan(self, file_path: Path) -> bool:
        for part in file_path.parent.parts:
            if part in self.exclude_dirs:
                return False
        return file_path.suffix == '.py'

    def get_module_name(self, file_path: Path) -> str:
        rel_path = file_path.relative_to(self.root_dir)
        return str(rel_path.with_suffix('')).replace(os.sep, '.')

    def scan_file(self, file_path: Path):
        if not self.should_scan(file_path):
            return

        self.results["total_files"] += 1
        module_name = self.get_module_name(file_path)
        status = "PASS"
        message = ""
        error_type = None
        has_error = False

        # --- 1. 문법 검사 (AST) ---
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            ast.parse(source, filename=str(file_path))
        except SyntaxError as e:
            status = "FAIL"
            error_type = "SyntaxError"
            message = f"Line {e.lineno}: {e.msg}"
            has_error = True

        # --- 2. 임포트 검사 (Import) ---
        if not has_error:
            try:
                importlib.import_module(module_name)
            except ImportError as e:
                status = "FAIL"
                error_type = "ImportError"
                message = str(e).split('\n')[0]
                has_error = True
            except Exception as e:
                status = "FAIL"
                error_type = "RuntimeError"
                message = str(e).split('\n')[0]
                has_error = True

        # --- 3. 🔥 pyflakes 검사 (NameError, 미사용 임포트 등) ---
        if not has_error and HAS_PYFLAKES:
            try:
                # pyflakes는 파일 내용을 직접 받아서 검사
                with open(file_path, 'r', encoding='utf-8') as f:
                    source = f.read()
                
                # 결과를 수집할 리스트
                errors = []
                def reporter_func(msg):
                    errors.append(msg)
                
                # pyflakes 실행 (기본 리포터 사용)
                pyflakes.api.check(source, str(file_path), reporter_func)
                
                if errors:
                    status = "FAIL" if status == "PASS" else status
                    error_type = "Pyflakes"
                    # 에러 메시지 요약 (첫 번째 에러만 표시, 나머지는 details에 저장)
                    first_err = str(errors[0]).split('\n')[0]
                    message = f"Line {first_err}" if 'line' in first_err else first_err
                    # details에 전체 에러 저장
                    for err in errors:
                        self.results["details"].append({
                            "file": str(file_path.relative_to(self.root_dir)),
                            "module": module_name,
                            "status": "FAIL",
                            "type": "Pyflakes",
                            "message": str(err)
                        })
                    self.results["pyflakes_errors"] += 1
                    has_error = True
            except Exception as e:
                # pyflakes 자체 오류는 무시 (다른 검사는 유효)
                print_warn(f"pyflakes 실행 중 오류 ({file_path.name}): {e}")

        # --- 4. 결과 저장 및 출력 ---
        if has_error:
            self.results["details"].append({
                "file": str(file_path.relative_to(self.root_dir)),
                "module": module_name,
                "status": status,
                "type": error_type,
                "message": message
            })
            if "Syntax" in error_type:
                self.results["syntax_errors"] += 1
                print_fail(f"{file_path.name} (문법 오류): {message}")
            elif "Import" in error_type:
                self.results["import_errors"] += 1
                print_fail(f"{file_path.name} (임포트 오류): {message}")
            elif "Pyflakes" in error_type:
                # 이미 details에 추가했으므로 상세 출력은 생략 (중복 방지)
                pass
            else:
                self.results["other_errors"] += 1
                print_fail(f"{file_path.name} (기타 오류): {message}")
        else:
            self.results["passed"] += 1
            print_ok(f"{file_path.name} ({module_name})")

    def run(self):
        print_title("전체 프로젝트 파일 구조 검사 시작 (v2.0 Pyflakes 통합)")
        print(f"기준 디렉토리: {self.root_dir}")
        print(f"제외 폴더: {', '.join(self.exclude_dirs)}")
        if not HAS_PYFLAKES:
            print_warn("🔥 pyflakes 미설치! (pip install pyflakes) → NameError/미사용 임포트 검사 불가")
        print("-" * 60)

        py_files = list(self.root_dir.rglob("*.py"))
        filtered_files = [f for f in py_files if self.should_scan(f)]

        print_info(f"총 발견된 .py 파일: {len(py_files)}개")
        print_info(f"검사 대상 파일: {len(filtered_files)}개")
        print("-" * 60)

        for file_path in filtered_files:
            self.scan_file(file_path)

        # 결과 요약
        print("\n" + "=" * 60)
        print(f"{Colors.BOLD}🏁 파일 검사 최종 리포트 (v2.0){Colors.END}")
        print("=" * 60)
        print(f"  총 검사 파일: {self.results['total_files']}개")
        print(f"  {Colors.GREEN}✅ 통과: {self.results['passed']}{Colors.END}")
        print(f"  {Colors.RED}❌ 문법 오류: {self.results['syntax_errors']}{Colors.END}")
        print(f"  {Colors.RED}❌ 임포트 오류: {self.results['import_errors']}{Colors.END}")
        if HAS_PYFLAKES:
            print(f"  {Colors.RED}❌ Pyflakes 경고(미사용/NameError): {self.results['pyflakes_errors']}{Colors.END}")
        print(f"  {Colors.YELLOW}⚠️ 기타 오류: {self.results['other_errors']}{Colors.END}")

        total_fails = self.results['syntax_errors'] + self.results['import_errors'] + self.results['pyflakes_errors'] + self.results['other_errors']
        if total_fails == 0:
            print(f"\n {Colors.GREEN}{Colors.BOLD}🎉 모든 파일이 완벽합니다! (숨은 NameError/미사용 임포트 없음){Colors.END}")
        else:
            print(f"\n {Colors.RED}{Colors.BOLD}🚨 {total_fails}개 파일에 문제가 발견되었습니다.{Colors.END}")
            print("   위에 표시된 빨간색(❌) 오류를 먼저 수정하세요.")

        # 상세 보고서 저장
        report_path = self.root_dir / "logs" / f"file_scan_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
            print(f"\n📄 상세 보고서 저장됨: {report_path}")
        except Exception as e:
            print_warn(f"보고서 저장 실패: {e}")
        print("=" * 60)

if __name__ == "__main__":
    os.system('color')
    try:
        scanner = ProjectFileScanner()
        scanner.run()
    except KeyboardInterrupt:
        print("\n🛑 사용자 중단")
    except Exception as e:
        print(f"💥 스캐너 자체 오류: {e}")
        traceback.print_exc()