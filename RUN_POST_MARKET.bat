@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title SDE Swing - Post Market

:MENU
cls
echo ================================================================
echo                  SDE SWING - POST MARKET
echo ================================================================
echo.
echo [1] Normal - refresh teknikal dan kirim ringkasan
echo [2] Preview existing - tidak refresh dan tidak kirim
echo [3] Kirim ulang existing - tidak refresh Yahoo
echo [0] Kembali
echo.
set "MODE="
set /p "MODE=Pilih mode: "
if "%MODE%"=="0" exit /b 0
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  goto MENU
)
if "%MODE%"=="1" set "ARGS=--job post_market"
if "%MODE%"=="2" set "ARGS=--job post_market --preview-existing --dry-run --force"
if "%MODE%"=="3" set "ARGS=--job post_market --preview-existing --force"
if not defined ARGS goto MENU
%SDE_PYTHON_CMD% run_sde_job.py %ARGS%
set "RC=%ERRORLEVEL%"
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --job post_market
echo.
echo Exit code: %RC%
set "ARGS="
pause
goto MENU
