@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ============================================
echo  stock_analyzer V10 시작 (app/main.py)
echo ============================================
python app/main.py
pause
