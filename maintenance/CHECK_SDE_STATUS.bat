@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title SDE Swing - Status
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 9009
)
echo ================================================================
echo                    STATUS SDE SWING
echo ================================================================
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --jobs market_outlook post_market final_watchlist
echo.
pause
exit /b 0
