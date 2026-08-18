#!/usr/bin/env python3
"""
diagnose_system.py - v1.3 (UTF-8 강제, ASCII 태그)
"""
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

import os
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json
import asyncio
import importlib
import subprocess
import re
from datetime import datetime
from typing import Dict, List, Tuple, Optional

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_ok(msg): print(f" {Colors.GREEN}[PASS]{Colors.END} {msg}")
def print_fail(msg): print(f" {Colors.RED}[FAIL]{Colors.END} {msg}")
def print_warn(msg): print(f" {Colors.YELLOW}[WARN]{Colors.END} {msg}")
def print_info(msg): print(f" {Colors.BLUE}[INFO]{Colors.END} {msg}")
def print_title(msg): print(f"\n{Colors.BOLD}[DIAG] {msg}{Colors.END}")

class SystemDiagnostic:
    def __init__(self):
        self.root_dir = _PROJECT_ROOT
        self.results = {"passed": 0, "failed": 0, "warnings": 0, "details": []}
        self.kiwoom_token = None

    def log_result(self, test_name: str, status: str, message: str, suggestion: str = ""):
        self.results["details"].append({"test": test_name, "status": status, "message": message, "suggestion": suggestion})
        if status == "PASS":
            self.results["passed"] += 1
            print_ok(f"{test_name}: {message}")
        elif status == "FAIL":
            self.results["failed"] += 1
            print_fail(f"{test_name}: {message}")
            if suggestion: print(f"      -> [TIP] {suggestion}")
        else:
            self.results["warnings"] += 1
            print_warn(f"{test_name}: {message}")
            if suggestion: print(f"      -> [TIP] {suggestion}")

    def test_python_version(self):
        v = sys.version_info
        if v.major >= 3 and v.minor >= 9:
            self.log_result("Python 버전", "PASS", f"{v.major}.{v.minor}.{v.micro}")
        else:
            self.log_result("Python 버전", "FAIL", f"{v.major}.{v.minor}.{v.micro} (3.9+ 필요)", "Python 3.12 설치")

    def test_directory_structure(self):
        required = ["core/logger.py", "core/config.py", "data/kiwoom_connector.py", "scanner_main.py", ".env"]
        missing = [p for p in required if not (self.root_dir / p).exists()]
        if not missing:
            self.log_result("폴더/파일 구조", "PASS", f"필수 파일 {len(required)}개 존재")
        else:
            self.log_result("폴더/파일 구조", "FAIL", f"{len(missing)}개 누락: {', '.join(missing[:3])}", "파일 복구 필요")

    def test_env_variables(self):
        from dotenv import load_dotenv
        load_dotenv()
        required = ['KIWOOM_APP_KEY', 'KIWOOM_APP_SECRET', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID']
        missing = [k for k in required if not os.getenv(k)]
        if not missing:
            self.log_result("환경변수 (.env)", "PASS", "모든 필수 키 존재")
        else:
            self.log_result("환경변수 (.env)", "FAIL", f"누락: {', '.join(missing)}", ".env 확인")

    def test_imports(self):
        modules = [("core.logger", "setup_logger"), ("data.kiwoom_connector", "KiwoomConnectorV512")]
        failed = []
        for m, c in modules:
            try:
                mod = importlib.import_module(m)
                if c and not hasattr(mod, c): raise AttributeError
            except Exception as e:
                failed.append(f"{m} -> {str(e)[:30]}")
        if not failed:
            self.log_result("모듈 임포트", "PASS", f"{len(modules)}개 정상")
        else:
            self.log_result("모듈 임포트", "FAIL", f"{len(failed)}개 실패", "pip install -r requirements.txt")

    def test_config_load(self):
        try:
            from core.config import get_config
            cfg = get_config()
            if cfg.get('ws_url'):
                self.log_result("설정 로드", "PASS", "config.yaml 정상")
            else:
                self.log_result("설정 로드", "WARN", "WS_URL 없음", "config/config.yaml 확인")
        except Exception as e:
            self.log_result("설정 로드", "FAIL", str(e), "config.yaml 검증")

    async def test_database(self):
        try:
            from data.db_manager import DatabaseManager
            db = DatabaseManager()
            await db.init_db()
            self.log_result("데이터베이스", "PASS", "DB 연결 성공")
            await db.close()
        except Exception as e:
            self.log_result("데이터베이스", "FAIL", str(e), "sqlite3 권한 확인")

    async def test_kiwoom_token(self):
        try:
            import aiohttp
            from dotenv import load_dotenv
            load_dotenv()
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.kiwoom.com/oauth2/token",
                    json={"grant_type": "client_credentials", "appkey": os.getenv("KIWOOM_APP_KEY"), "secretkey": os.getenv("KIWOOM_APP_SECRET")},
                    timeout=10
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("token"):
                            self.log_result("키움 API 토큰", "PASS", "토큰 발급 성공")
                        else:
                            self.log_result("키움 API 토큰", "FAIL", "응답에 토큰 없음", "API 키 확인")
                    else:
                        self.log_result("키움 API 토큰", "FAIL", f"HTTP {resp.status}", "네트워크 확인")
        except Exception as e:
            self.log_result("키움 API 토큰", "FAIL", str(e), "인터넷 연결 확인")

    def test_blackbox_write(self):
        try:
            from core.blackbox_logger import log_raw_data
            log_raw_data("TEST", source="DIAG")
            self.log_result("블랙박스 로깅", "PASS", "로그 쓰기 성공")
        except Exception as e:
            self.log_result("블랙박스 로깅", "FAIL", str(e), "logs/blackbox 권한 확인")

    def test_scheduler(self):
        try:
            import apscheduler
            self.log_result("APScheduler", "PASS", f"버전 {apscheduler.__version__}")
        except ImportError:
            self.log_result("APScheduler", "FAIL", "패키지 없음", "pip install apscheduler")

    def test_dependencies(self):
        try:
            req_file = self.root_dir / "requirements.txt"
            if not req_file.exists():
                self.log_result("의존성 패키지", "WARN", "requirements.txt 없음", "선택 확인")
                return
            with open(req_file) as f:
                reqs = [re.split(r'[>=<~!]', line.strip())[0] for line in f if line.strip() and not line.startswith('#')]
            installed = subprocess.check_output([sys.executable, '-m', 'pip', 'freeze']).decode().split('\n')
            installed_names = [p.split('==')[0] for p in installed if '==' in p]
            missing = [p for p in reqs if p not in installed_names]
            if not missing:
                self.log_result("의존성 패키지", "PASS", f"{len(reqs)}개 모두 설치됨")
            else:
                self.log_result("의존성 패키지", "WARN", f"누락: {', '.join(missing[:3])}", "pip install -r requirements.txt")
        except Exception as e:
            self.log_result("의존성 패키지", "WARN", f"pip 실행 실패: {e}", "pip 경로 확인")

    def print_summary(self):
        print("\n" + "=" * 60)
        print(f"{Colors.BOLD}[FINAL] 진단 리포트{Colors.END}")
        print("=" * 60)
        total = self.results["passed"] + self.results["failed"] + self.results["warnings"]
        print(f"  총 테스트: {total}개")
        print(f"  {Colors.GREEN}[PASS]: {self.results['passed']}{Colors.END}")
        print(f"  {Colors.YELLOW}[WARN]: {self.results['warnings']}{Colors.END}")
        print(f"  {Colors.RED}[FAIL]: {self.results['failed']}{Colors.END}")
        if self.results["failed"] == 0:
            print(f"\n {Colors.GREEN}{Colors.BOLD}[SUCCESS] 모든 핵심 테스트 통과!{Colors.END}")
        else:
            print(f"\n {Colors.RED}{Colors.BOLD}[FAIL] {self.results['failed']}개 실패! 조치 필요.{Colors.END}")
        report_path = self.root_dir / "logs" / f"diagnostic_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
            print(f"\n[INFO] 보고서 저장됨: {report_path}")
        except: pass
        print("=" * 60)

async def run_async(diag):
    await diag.test_database()
    await diag.test_kiwoom_token()

def main():
    os.system('color')
    print_title("시스템 전신 진단 시작")
    print(f"기준: {_PROJECT_ROOT}")
    print("-" * 60)
    diag = SystemDiagnostic()
    diag.test_python_version()
    diag.test_directory_structure()
    diag.test_env_variables()
    diag.test_imports()
    diag.test_config_load()
    diag.test_blackbox_write()
    diag.test_scheduler()
    diag.test_dependencies()
    print("\n" + "-" * 60)
    print_info("Async 테스트 실행 중...")
    asyncio.run(run_async(diag))
    diag.print_summary()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP] 사용자 중단")
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback; traceback.print_exc()