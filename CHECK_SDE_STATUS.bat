@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title SDE Swing - Status Semua Job

call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 9009
)

for %%J in (pre_market market_outlook post_market broker_summary broker_multi_day final_watchlist full_manual) do (
  echo.
  %SDE_PYTHON_CMD% tools\print_job_status.py --job %%J
)
set "RC=%ERRORLEVEL%"
echo.
echo Exit code: %RC%
pause
exit /b %RC%
