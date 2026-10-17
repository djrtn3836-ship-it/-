@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m scheduler.backfill_supervisor >> logs\supply_backfill_console.log 2>&1
