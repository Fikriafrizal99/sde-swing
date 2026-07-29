@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title SDE Swing - Final Watchlist

:MENU
cls
echo ================================================================
echo                SDE SWING - FINAL WATCHLIST
echo ================================================================
echo.
echo [1] Normal - broker break jika data hari ini belum tersedia
echo [2] Preview - tidak kirim Telegram
echo [3] Kirim ulang - broker break bila perlu + force resend
echo [4] Cek status Final Watchlist
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
if "%MODE%"=="4" (
  %SDE_PYTHON_CMD% tools\print_job_status.py --job final_watchlist
  pause
  goto MENU
)
set "ARGS="
if "%MODE%"=="1" set "ARGS=--job final_watchlist --interactive-broker"
if "%MODE%"=="2" set "ARGS=--job final_watchlist --dry-run --force"
if "%MODE%"=="3" set "ARGS=--job final_watchlist --interactive-broker --force"
if not defined ARGS goto MENU
%SDE_PYTHON_CMD% run_sde_job.py %ARGS%
set "RC=%ERRORLEVEL%"
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --job final_watchlist
echo.
echo Exit code: %RC%
pause
goto MENU
