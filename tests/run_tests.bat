@echo off
chcp 65001 >nul
title 통합 검증 실행기

echo ============================================================
echo 🧪 [통합 검증 실행기] 배치 파일 시작 (UTF-8 강제)
echo ============================================================

cd /d "%~dp0"
echo 현재 디렉토리: %cd%

echo.
echo 📌 Python 실행기 시작...
python run_all_tests.py

if %errorlevel% equ 0 (
    echo.
    echo ✅ 모든 테스트 통과!
) else (
    echo.
    echo ⚠️ 일부 테스트가 실패했습니다. 위 로그를 확인하세요.
)

echo.
echo ============================================================
pause