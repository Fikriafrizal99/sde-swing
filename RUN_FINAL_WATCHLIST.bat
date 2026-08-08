@echo off
setlocal EnableExtensions EnableDelayedExpansion
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
echo [3] Kirim ulang - pakai artifact hari trading terakhir + force resend
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
if "%MODE%"=="3" (
  set "RESEND_DATE="
  for /f "usebackq delims=" %%D in (`%SDE_PYTHON_CMD% tools\resolve_last_trading_day.py`) do set "RESEND_DATE=%%D"
  if not defined RESEND_DATE (
    echo Gagal menentukan hari trading terakhir.
    pause
    goto MENU
  )
  echo.
  echo Mengirim ulang Final Watchlist dari artifact trade date !RESEND_DATE!...
  set "ARGS=--job final_watchlist --preview-existing --force --trade-date !RESEND_DATE!"
)
if not defined ARGS goto MENU
%SDE_PYTHON_CMD% run_sde_job.py %ARGS%
set "RC=%ERRORLEVEL%"
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --job final_watchlist
echo.
echo Exit code: %RC%
pause
goto MENU
