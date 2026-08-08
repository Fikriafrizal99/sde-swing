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
echo              SDE SWING - BROKER OPERATIONS
echo ================================================================
echo.
echo [1] Broker Summary
echo [2] Broker Multi-Day
echo [3] Backfill Broker Portfolio
echo [4] Cek status Broker Summary
echo [5] Cek status Broker Multi-Day
echo [0] Kembali
echo.
set "CHOICE="
set /p "CHOICE=Pilih menu: "
if "%CHOICE%"=="1" goto SUMMARY
if "%CHOICE%"=="2" goto MULTIDAY
if "%CHOICE%"=="3" goto BACKFILL
if "%CHOICE%"=="4" goto STATUS_SUMMARY
if "%CHOICE%"=="5" goto STATUS_MULTI
if "%CHOICE%"=="0" exit /b 0
goto MENU

:SUMMARY
cls
%SDE_PYTHON_CMD% -u run_sde_job_integrated.py --job broker_summary
set "RC=!ERRORLEVEL!"
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --job broker_summary
echo Exit code: !RC!
pause
goto MENU

:MULTIDAY
cls
%SDE_PYTHON_CMD% -u run_sde_job_integrated.py --job broker_multi_day
set "RC=!ERRORLEVEL!"
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --job broker_multi_day
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

:STATUS_MULTI
%SDE_PYTHON_CMD% tools\print_job_status.py --job broker_multi_day
pause
goto MENU
