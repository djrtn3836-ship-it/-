#!/usr/bin/env python3
"""
🔬 PROJECT-WIDE FILE SCANNER v1.1 (전체 파일 일괄 문법/임포트 검사기 - 버그 수정)
설명: 프로젝트 내 모든 *.py 파일을 찾아서:
      1) 문법 오류(SyntaxError) 검사
      2) 임포트 오류(ImportError / ModuleNotFoundError) 검사
      3) 기타 실행 중 발생할 수 있는 예외 포착
사용법: python scan_all_files.py
"""
import os
import sys
import ast
import importlib
import traceback
import json
from pathlib import Path
from datetime import datetime  # 🔥 여기서 한 번만 임포트
from typing import List, Dict, Tuple

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

# ============================================================
# 스캐너 메인 클래스
# ============================================================
class ProjectFileScanner:
    def __init__(self):
        self.root_dir = Path(__file__).parent
        self.results = {
            "total_files": 0,
            "passed": 0,
            "syntax_errors": 0,
            "import_errors": 0,
            "other_errors": 0,
            "details": []
        }
        # 검사에서 제외할 디렉토리 (가상환경, 캐시 등)
        self.exclude_dirs = {
            '.git', '__pycache__', 'venv', 'env', 
            'logs', 'reports', 'fonts', '.pytest_cache'
        }

    def should_scan(self, file_path: Path) -> bool:
        """해당 파일을 검사할지 결정 (제외 디렉토리 필터링)"""
        for part in file_path.parent.parts:
            if part in self.exclude_dirs:
                return False
        return file_path.suffix == '.py'

    def get_module_name(self, file_path: Path) -> str:
        """파일 경로를 Python 모듈 경로(import 구문)로 변환"""
        rel_path = file_path.relative_to(self.root_dir)
        module_str = str(rel_path.with_suffix(''))
        return module_str.replace(os.sep, '.')

    def scan_file(self, file_path: Path):
        """단일 파일 검사 (문법 + 임포트)"""
        if not self.should_scan(file_path):
            return

        self.results["total_files"] += 1
        module_name = self.get_module_name(file_path)
        status = "PASS"
        message = ""
        error_type = None

        try:
            # 1. 문법 검사 (AST 파싱)
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            ast.parse(source, filename=str(file_path))

            # 2. 임포트 검사 (모듈 실제 로드)
            try:
                importlib.import_module(module_name)
            except ImportError as e:
                status = "FAIL"
                error_type = "ImportError"
                message = str(e).split('\n')[0]
            except Exception as e:
                status = "FAIL"
                error_type = "RuntimeError"
                message = str(e).split('\n')[0]

        except SyntaxError as e:
            status = "FAIL"
            error_type = "SyntaxError"
            message = f"Line {e.lineno}: {e.msg}"
        except Exception as e:
            status = "FAIL"
            error_type = "UnknownError"
            message = str(e).split('\n')[0]

        # 결과 저장
        self.results["details"].append({
            "file": str(file_path.relative_to(self.root_dir)),
            "module": module_name,
            "status": status,
            "type": error_type,
            "message": message
        })

        if status == "PASS":
            self.results["passed"] += 1
            print_ok(f"{file_path.name} ({module_name})")
        else:
            if "Syntax" in error_type:
                self.results["syntax_errors"] += 1
                print_fail(f"{file_path.name} (문법 오류): {message}")
            elif "Import" in error_type:
                self.results["import_errors"] += 1
                print_fail(f"{file_path.name} (임포트 오류): {message}")
                print_info(f"   → {module_name} 에서 발생")
            else:
                self.results["other_errors"] += 1
                print_fail(f"{file_path.name} (기타 오류): {message}")

    def run(self):
        """모든 파일 순회 및 스캔 실행"""
        print_title("전체 프로젝트 파일 구조 검사 시작")
        print(f"기준 디렉토리: {self.root_dir}")
        print(f"제외 폴더: {', '.join(self.exclude_dirs)}")
        print("-" * 60)

        # 모든 .py 파일 순회
        py_files = list(self.root_dir.rglob("*.py"))
        filtered_files = [f for f in py_files if self.should_scan(f)]

        print_info(f"총 발견된 .py 파일: {len(py_files)}개")
        print_info(f"검사 대상 파일: {len(filtered_files)}개 (나머지는 제외됨)")
        print("-" * 60)

        for file_path in filtered_files:
            self.scan_file(file_path)

        # 결과 요약
        print("\n" + "=" * 60)
        print(f"{Colors.BOLD}🏁 파일 검사 최종 리포트{Colors.END}")
        print("=" * 60)
        print(f"  총 검사 파일: {self.results['total_files']}개")
        print(f"  {Colors.GREEN}✅ 통과: {self.results['passed']}{Colors.END}")
        
        if self.results['syntax_errors'] > 0:
            print(f"  {Colors.RED}❌ 문법 오류: {self.results['syntax_errors']}{Colors.END}")
        else:
            print(f"  {Colors.GREEN}❌ 문법 오류: 0{Colors.END}")

        if self.results['import_errors'] > 0:
            print(f"  {Colors.RED}❌ 임포트 오류: {self.results['import_errors']}{Colors.END}")
        else:
            print(f"  {Colors.GREEN}❌ 임포트 오류: 0{Colors.END}")

        if self.results['other_errors'] > 0:
            print(f"  {Colors.YELLOW}⚠️ 기타 오류: {self.results['other_errors']}{Colors.END}")
        else:
            print(f"  {Colors.GREEN}⚠️ 기타 오류: 0{Colors.END}")

        if self.results['syntax_errors'] == 0 and self.results['import_errors'] == 0 and self.results['other_errors'] == 0:
            print(f"\n {Colors.GREEN}{Colors.BOLD}🎉 모든 파일이 문법적으로 완벽합니다! (숨은 오류 없음){Colors.END}")
        else:
            print(f"\n {Colors.RED}{Colors.BOLD}🚨 {self.results['syntax_errors'] + self.results['import_errors'] + self.results['other_errors']}개 파일에 문제가 발견되었습니다.{Colors.END}")
            print("   위에 표시된 빨간색(❌) 오류를 먼저 수정하세요.")

        # 🔥 상세 보고서 저장 (datetime 버그 수정됨)
        try:
            report_dir = self.root_dir / "logs"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / f"file_scan_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
            print(f"\n📄 상세 보고서 저장됨: {report_path}")
        except Exception as e:
            print_warn(f"보고서 저장 실패: {e}")
        print("=" * 60)

# ============================================================
# 실행
# ============================================================
if __name__ == "__main__":
    os.system('color')
    try:
        scanner = ProjectFileScanner()
        scanner.run()
    except KeyboardInterrupt:
        print("\n🛑 사용자 중단")
    except Exception as e:
        print(f"💥 스캐너 자체 오류: {e}")
        import traceback
        traceback.print_exc()