@echo off
REM scripts/run_lint.bat - Windows용 린트 실행 스크립트

echo ========================================
echo 🧹 Ruff 린트 실행 중...
echo ========================================
ruff check . --fix

echo.
echo ========================================
echo 🎨 Ruff 포맷 적용 중...
echo ========================================
ruff format .

echo.
echo ========================================
echo 🔍 mypy 타입 검사 실행 중...
echo ========================================
mypy core/ data/ scanner/ strategy/ orchestrator/ risk/ report/ filters/ feedback/ scheduler/ collector/ analytics/ validation/ decision/

echo.
echo ========================================
echo ✅ 린트 완료!
echo ========================================
pause
