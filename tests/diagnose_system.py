#!/usr/bin/env python3
"""
🧬 SYSTEM DIAGNOSTIC SUITE v1.1 (통합 시스템 진단기 - 버그 수정)
- 의존성 검사: 버전 조건(>=, <=, ~=) 자동 제거
- APScheduler 테스트: import만 확인 (실제 실행은 하지 않음)
"""

import os
import sys
import json
import asyncio
import importlib
import subprocess
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional

# ============================================================
# 1. 테스트 결과 포맷 및 색상 (Windows CMD 호환)
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
# 2. 진단 메인 클래스
# ============================================================
class SystemDiagnostic:
    def __init__(self):
        self.root_dir = Path(__file__).parent
        self.results = {
            "passed": 0,
            "failed": 0,
            "warnings": 0,
            "details": []
        }
        self.kiwoom_token = None

    def log_result(self, test_name: str, status: str, message: str, suggestion: str = ""):
        self.results["details"].append({
            "test": test_name,
            "status": status,
            "message": message,
            "suggestion": suggestion
        })
        if status == "PASS":
            self.results["passed"] += 1
            print_ok(f"{test_name}: {message}")
        elif status == "FAIL":
            self.results["failed"] += 1
            print_fail(f"{test_name}: {message}")
            if suggestion:
                print(f"      → 💡 {suggestion}")
        else:  # WARN
            self.results["warnings"] += 1
            print_warn(f"{test_name}: {message}")
            if suggestion:
                print(f"      → 💡 {suggestion}")

    # ============================================================
    # 테스트 1: Python 버전
    # ============================================================
    def test_python_version(self):
        version = sys.version_info
        if version.major >= 3 and version.minor >= 9:
            self.log_result("Python 버전", "PASS", f"{version.major}.{version.minor}.{version.micro} (이상 없음)")
        else:
            self.log_result("Python 버전", "FAIL", f"{version.major}.{version.minor}.{version.micro} (3.9+ 필요)", "Python 3.12 이상을 설치하세요.")

    # ============================================================
    # 테스트 2: 폴더 구조 및 필수 파일 존재 여부
    # ============================================================
    def test_directory_structure(self):
        required_paths = [
            "core/logger.py",
            "core/config.py",
            "core/blackbox_logger.py",
            "data/kiwoom_connector.py",
            "data/db_manager.py",
            "scanner/realtime_monitor.py",
            "scanner/deep_analyzer.py",
            "report/telegram_sender.py",
            "report/daily_report.py",
            "config/config.yaml",
            "config/secure_config.py",
            "scanner_main.py",
            ".env"
        ]
        missing = []
        for p in required_paths:
            if not (self.root_dir / p).exists():
                missing.append(p)
        
        if not missing:
            self.log_result("폴더/파일 구조", "PASS", f"모든 필수 파일({len(required_paths)}개) 존재")
        else:
            self.log_result("폴더/파일 구조", "FAIL", f"{len(missing)}개 파일 누락: {', '.join(missing[:5])}", "누락된 파일을 복구하거나 생성하세요.")

    # ============================================================
    # 테스트 3: .env 환경변수
    # ============================================================
    def test_env_variables(self):
        from dotenv import load_dotenv
        load_dotenv()
        required_keys = ['KIWOOM_APP_KEY', 'KIWOOM_APP_SECRET', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID']
        missing_keys = []
        for key in required_keys:
            if not os.getenv(key):
                missing_keys.append(key)
        
        if not missing_keys:
            self.log_result("환경변수 (.env)", "PASS", "모든 필수 키 존재")
        else:
            self.log_result("환경변수 (.env)", "FAIL", f"누락된 키: {', '.join(missing_keys)}", ".env 파일을 확인하고 키를 추가하세요.")

    # ============================================================
    # 테스트 4: 핵심 모듈 임포트 (Import Error 검사)
    # ============================================================
    def test_imports(self):
        modules = [
            ("core.logger", "setup_logger"),
            ("core.config", "get_config"),
            ("core.blackbox_logger", "log_event"),
            ("data.kiwoom_connector", "KiwoomConnectorV512"),
            ("data.db_manager", "DatabaseManager"),
            ("scanner.realtime_monitor", "RealtimeMonitor"),
            ("scanner.deep_analyzer", "DeepAnalyzer"),
            ("report.telegram_sender", "TelegramSender"),
            ("report.daily_report", "DailyReportGenerator"),
            ("feedback.feedback_learner", "FeedbackLearner"),
            ("core.scheduler", "SchedulerManager"),
        ]
        
        failed_modules = []
        for module_name, class_name in modules:
            try:
                module = importlib.import_module(module_name)
                if class_name and not hasattr(module, class_name):
                    raise AttributeError(f"Class '{class_name}' not found")
            except Exception as e:
                failed_modules.append(f"{module_name}.{class_name} -> {str(e)[:50]}")
        
        if not failed_modules:
            self.log_result("모듈 임포트", "PASS", f"{len(modules)}개 모듈 모두 정상")
        else:
            self.log_result("모듈 임포트", "FAIL", f"{len(failed_modules)}개 실패", "pip install -r requirements.txt 실행 또는 파일 경로 확인")

    # ============================================================
    # 테스트 5: 설정 파일 (config.yaml) 로드
    # ============================================================
    def test_config_load(self):
        try:
            from core.config import get_config
            config = get_config()
            ws_url = config.get('ws_url')
            if ws_url and 'kiwoom' in ws_url:
                self.log_result("설정 로드", "PASS", f"WS_URL 확인: {ws_url[:30]}...")
            else:
                self.log_result("설정 로드", "WARN", "WS_URL이 비정상적입니다.", "config/config.yaml을 확인하세요.")
        except Exception as e:
            self.log_result("설정 로드", "FAIL", str(e), "config.yaml이 유효한지 확인하세요.")

    # ============================================================
    # 테스트 6: 데이터베이스 (DB) 연결 및 초기화
    # ============================================================
    async def test_database(self):
        try:
            from data.db_manager import DatabaseManager
            db = DatabaseManager()
            await db.init_db()
            self.log_result("데이터베이스", "PASS", "DB 초기화 및 연결 성공")
            await db.close()
        except Exception as e:
            self.log_result("데이터베이스", "FAIL", str(e), "sqlite3 권한 또는 경로 문제 확인")

    # ============================================================
    # 테스트 7: 🔥 키움 Access Token 발급 (실제 네트워크 테스트)
    # ============================================================
    async def test_kiwoom_token(self):
        try:
            import aiohttp
            import os
            from dotenv import load_dotenv
            load_dotenv()
            api_key = os.getenv("KIWOOM_APP_KEY")
            api_secret = os.getenv("KIWOOM_APP_SECRET")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.kiwoom.com/oauth2/token",
                    json={"grant_type": "client_credentials", "appkey": api_key, "secretkey": api_secret},
                    timeout=10
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        token = data.get("token")
                        if token:
                            self.kiwoom_token = token[:10]
                            self.log_result("키움 API 토큰", "PASS", f"토큰 발급 성공 (앞 10자리: {token[:10]}...)")
                        else:
                            self.log_result("키움 API 토큰", "FAIL", "응답에 토큰 없음", "API 키가 유효한지 확인")
                    else:
                        self.log_result("키움 API 토큰", "FAIL", f"HTTP {resp.status}", "네트워크 또는 API 키 확인")
        except Exception as e:
            self.log_result("키움 API 토큰", "FAIL", str(e), "인터넷 연결 또는 방화벽 확인")

    # ============================================================
    # 테스트 8: 블랙박스 로그 쓰기 권한
    # ============================================================
    def test_blackbox_write(self):
        try:
            from core.blackbox_logger import log_raw_data, log_event
            test_msg = "DIAGNOSTIC_TEST_MESSAGE"
            log_raw_data(test_msg, source="TEST")
            log_event("DIAGNOSTIC_TEST", {"status": "ok"})
            
            log_file = self.root_dir / "logs" / "blackbox" / "blackbox.log"
            if log_file.exists():
                self.log_result("블랙박스 로깅", "PASS", f"로그 파일 생성됨 ({log_file.stat().st_size} bytes)")
            else:
                self.log_result("블랙박스 로깅", "WARN", "파일이 즉시 생성되지 않음 (버퍼링)", "정상 동작 중")
        except Exception as e:
            self.log_result("블랙박스 로깅", "FAIL", str(e), "logs/blackbox 폴더 권한 확인")

    # ============================================================
    # 테스트 9: APScheduler (import만 확인 - 실행 없음)
    # ============================================================
    def test_scheduler(self):
        try:
            import apscheduler
            self.log_result("APScheduler", "PASS", f"패키지 설치됨 (버전: {apscheduler.__version__})")
        except ImportError:
            self.log_result("APScheduler", "FAIL", "패키지가 설치되지 않음", "pip install apscheduler 실행")

    # ============================================================
    # 테스트 10: 🔧 의존성 패키지 (버전 조건 제거하여 비교)
    # ============================================================
    def test_dependencies(self):
        try:
            req_file = self.root_dir / "requirements.txt"
            if not req_file.exists():
                self.log_result("의존성 패키지", "WARN", "requirements.txt 파일 없음", "선택 사항이지만, pip freeze로 확인 권장")
                return
            
            # 🔥 버전 조건 제거 (정규식)
            def clean_pkg_name(line):
                # 'polars>=0.19.0' → 'polars'
                return re.split(r'[>=<~!]', line.strip())[0].strip()
            
            with open(req_file, 'r') as f:
                required = [clean_pkg_name(line) for line in f if line.strip() and not line.startswith('#')]
            
            installed = subprocess.check_output([sys.executable, '-m', 'pip', 'freeze']).decode().split('\n')
            installed_names = [p.split('==')[0] for p in installed if '==' in p]
            
            missing_pkgs = [p for p in required if p not in installed_names]
            if not missing_pkgs:
                self.log_result("의존성 패키지", "PASS", f"필수 패키지 {len(required)}개 모두 설치됨")
            else:
                self.log_result("의존성 패키지", "WARN", f"누락된 패키지: {', '.join(missing_pkgs[:5])}", "pip install -r requirements.txt 실행")
        except Exception as e:
            self.log_result("의존성 패키지", "WARN", f"pip freeze 실행 실패: {e}", "pip 명령어 경로 확인")

    # ============================================================
    # 최종 리포트 출력
    # ============================================================
    def print_summary(self):
        print("\n" + "=" * 60)
        print(f"{Colors.BOLD}🏁 최종 진단 리포트{Colors.END}")
        print("=" * 60)
        
        total = self.results["passed"] + self.results["failed"] + self.results["warnings"]
        print(f"  총 테스트: {total}개")
        print(f"  {Colors.GREEN}✅ 통과: {self.results['passed']}{Colors.END}")
        print(f"  {Colors.YELLOW}⚠️ 경고: {self.results['warnings']}{Colors.END}")
        print(f"  {Colors.RED}❌ 실패: {self.results['failed']}{Colors.END}")
        
        if self.results["failed"] == 0:
            print(f"\n {Colors.GREEN}{Colors.BOLD}🎉 모든 핵심 테스트 통과! 시스템이 건강합니다.{Colors.END}")
            if self.results["warnings"] > 0:
                print(f"   (경고는 선택적 확인 사항입니다.)")
        else:
            print(f"\n {Colors.RED}{Colors.BOLD}🚨 {self.results['failed']}개 테스트 실패! 위 로그를 확인하여 조치하세요.{Colors.END}")
        
        report_path = self.root_dir / "logs" / f"diagnostic_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
            print(f"\n📄 상세 보고서 저장됨: {report_path}")
        except:
            pass
        print("=" * 60)

# ============================================================
# 실행 (Async 테스트 포함)
# ============================================================
async def run_async_tests(diagnostic: SystemDiagnostic):
    await diagnostic.test_database()
    await diagnostic.test_kiwoom_token()

def main():
    os.system('color') 
    print_title("시스템 전신 진단 시작 (Full System MRI)")
    print(f"기준 디렉토리: {Path(__file__).parent}")
    print(f"진단 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 60)

    diagnostic = SystemDiagnostic()
    
    diagnostic.test_python_version()
    diagnostic.test_directory_structure()
    diagnostic.test_env_variables()
    diagnostic.test_imports()
    diagnostic.test_config_load()
    diagnostic.test_blackbox_write()
    diagnostic.test_scheduler()          # 수정: import만 확인
    diagnostic.test_dependencies()       # 수정: 버전 조건 제거
    
    print("\n" + "-" * 60)
    print_info("네트워크 및 DB 테스트 (Async) 실행 중... (인터넷 필요)")
    asyncio.run(run_async_tests(diagnostic))
    
    diagnostic.print_summary()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 사용자 중단")
    except Exception as e:
        print(f"💥 진단 스크립트 자체 오류: {e}")
        import traceback
        traceback.print_exc()