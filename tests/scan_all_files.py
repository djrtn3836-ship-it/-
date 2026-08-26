#!/usr/bin/env python3
"""
scan_all_files.py - v2.2 (UTF-8 강제, ASCII 태그)
"""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import ast
import importlib
import json
import traceback
from datetime import datetime

try:
    import pyflakes.api

    HAS_PYFLAKES = True
except:
    HAS_PYFLAKES = False


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    END = "\033[0m"


def print_ok(msg):
    print(f" {Colors.GREEN}[PASS]{Colors.END} {msg}")


def print_fail(msg):
    print(f" {Colors.RED}[FAIL]{Colors.END} {msg}")


def print_warn(msg):
    print(f" {Colors.YELLOW}[WARN]{Colors.END} {msg}")


def print_info(msg):
    print(f" {Colors.BLUE}[INFO]{Colors.END} {msg}")


def print_title(msg):
    print(f"\n{Colors.BOLD}[SCAN] {msg}{Colors.END}")


class ProjectFileScanner:
    def __init__(self):
        self.root_dir = _PROJECT_ROOT
        self.results = {
            "total_files": 0,
            "passed": 0,
            "syntax_errors": 0,
            "import_errors": 0,
            "pyflakes_errors": 0,
            "other_errors": 0,
            "details": [],
        }
        self.exclude_dirs = {".git", "__pycache__", "venv", "env", "logs", "reports", "fonts", ".pytest_cache"}

    def should_scan(self, p: Path) -> bool:
        for part in p.parent.parts:
            if part in self.exclude_dirs:
                return False
        return p.suffix == ".py"

    def get_module_name(self, p: Path) -> str:
        return str(p.relative_to(self.root_dir).with_suffix("")).replace(os.sep, ".")

    def scan_file(self, p: Path):
        if not self.should_scan(p):
            return
        self.results["total_files"] += 1
        mod = self.get_module_name(p)
        status, msg, err_type = "PASS", "", None
        has_err = False
        try:
            with open(p, encoding="utf-8") as f:
                source = f.read()
            ast.parse(source)
        except SyntaxError as e:
            status, err_type, msg = "FAIL", "SyntaxError", f"Line {e.lineno}: {e.msg}"
            has_err = True
        if not has_err:
            try:
                importlib.import_module(mod)
            except ImportError as e:
                status, err_type, msg = "FAIL", "ImportError", str(e).split("\n")[0]
                has_err = True
            except Exception as e:
                status, err_type, msg = "FAIL", "RuntimeError", str(e).split("\n")[0]
                has_err = True
        if not has_err and HAS_PYFLAKES:
            try:
                with open(p, encoding="utf-8") as f:
                    source = f.read()
                errors = []

                def reporter(m):
                    errors.append(m)

                pyflakes.api.check(source, str(p), reporter)
                if errors:
                    status, err_type = "FAIL", "Pyflakes"
                    msg = str(errors[0]).split("\n")[0]
                    for e in errors:
                        self.results["details"].append(
                            {
                                "file": str(p.relative_to(self.root_dir)),
                                "module": mod,
                                "status": "FAIL",
                                "type": "Pyflakes",
                                "message": str(e),
                            }
                        )
                    self.results["pyflakes_errors"] += 1
                    has_err = True
            except:
                pass
        if has_err:
            self.results["details"].append(
                {
                    "file": str(p.relative_to(self.root_dir)),
                    "module": mod,
                    "status": status,
                    "type": err_type,
                    "message": msg,
                }
            )
            if "Syntax" in err_type:
                self.results["syntax_errors"] += 1
                print_fail(f"{p.name} (문법): {msg}")
            elif "Import" in err_type:
                self.results["import_errors"] += 1
                print_fail(f"{p.name} (임포트): {msg}")
            elif "Pyflakes" in err_type:
                pass
            else:
                self.results["other_errors"] += 1
                print_fail(f"{p.name} (기타): {msg}")
        else:
            self.results["passed"] += 1
            print_ok(f"{p.name} ({mod})")

    def run(self):
        print_title("전체 파일 검사 시작")
        print(f"기준: {self.root_dir}")
        if not HAS_PYFLAKES:
            print_warn("pyflakes 미설치 -> 일부 검사 생략")
        print("-" * 60)
        py_files = list(self.root_dir.rglob("*.py"))
        targets = [f for f in py_files if self.should_scan(f)]
        print_info(f"대상 파일: {len(targets)}개")
        for f in targets:
            self.scan_file(f)
        print("\n" + "=" * 60)
        print(f"{Colors.BOLD}[RESULT] 파일 검사 최종{Colors.END}")
        print(f"  총 검사: {self.results['total_files']}개")
        print(f"  {Colors.GREEN}[PASS]: {self.results['passed']}{Colors.END}")
        print(f"  {Colors.RED}[FAIL] 문법: {self.results['syntax_errors']}{Colors.END}")
        print(f"  {Colors.RED}[FAIL] 임포트: {self.results['import_errors']}{Colors.END}")
        if HAS_PYFLAKES:
            print(f"  {Colors.RED}[FAIL] Pyflakes: {self.results['pyflakes_errors']}{Colors.END}")
        total_fails = (
            self.results["syntax_errors"]
            + self.results["import_errors"]
            + self.results["pyflakes_errors"]
            + self.results["other_errors"]
        )
        if total_fails == 0:
            print(f"\n {Colors.GREEN}{Colors.BOLD}[SUCCESS] 모든 파일 완벽!{Colors.END}")
        else:
            print(f"\n {Colors.RED}{Colors.BOLD}[FAIL] {total_fails}개 문제 발견.{Colors.END}")
        report_path = self.root_dir / "logs" / f"file_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
            print(f"\n[INFO] 보고서 저장: {report_path}")
        except:
            pass


if __name__ == "__main__":
    os.system("color")
    try:
        ProjectFileScanner().run()
    except KeyboardInterrupt:
        print("\n[STOP] 사용자 중단")
    except Exception as e:
        print(f"[ERROR] {e}")
        traceback.print_exc()
