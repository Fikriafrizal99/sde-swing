@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0\.."
title SDE Swing - Broker Operations

call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo [FAILED] Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 9009
)

:MENU
cls
echo ================================================================
echo              FTJ Community - BROKER OPERATIONS
echo ================================================================
echo.
echo [1] Preview Broker Summary
echo [2] Backfill Broker Portfolio
echo [3] Cek status Broker Summary
echo [0] Kembali
echo.
set "CHOICE="
set /p "CHOICE=Pilih menu: "
if "%CHOICE%"=="1" goto SUMMARY
if "%CHOICE%"=="2" goto BACKFILL
if "%CHOICE%"=="3" goto STATUS_SUMMARY
if "%CHOICE%"=="0" exit /b 0
goto MENU

:SUMMARY
cls
%SDE_PYTHON_CMD% -u run_sde_job_integrated.py --job broker_summary --no-telegram
set "RC=!ERRORLEVEL!"
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --job broker_summary
echo Exit code: !RC!
pause
goto MENU

:BACKFILL
call maintenance\BACKFILL_PORTFOLIO_BROKER.bat
goto MENU

:STATUS_SUMMARY
%SDE_PYTHON_CMD% tools\print_job_status.py --job broker_summary
pause
goto MENU
