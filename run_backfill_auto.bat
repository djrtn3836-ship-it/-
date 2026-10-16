@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m scheduler.supply_backfill >> logs\supply_backfill_console.log 2>&1
