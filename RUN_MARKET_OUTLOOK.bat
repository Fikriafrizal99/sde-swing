@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title SDE Swing - Market Outlook

:MENU
cls
echo ================================================================
echo                 SDE SWING - MARKET OUTLOOK
echo ================================================================
echo.
echo [1] Normal - refresh dan kirim Telegram
echo [2] Preview existing - tidak kirim Telegram
echo [3] Kirim ulang - delivery-only hasil hari trading terakhir
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
set "ARGS="
set "RESEND_ONLY=0"
if "%MODE%"=="1" set "ARGS=--job market_outlook"
if "%MODE%"=="2" set "ARGS=--job market_outlook --preview-existing --dry-run --force"
if "%MODE%"=="3" (
  set "RESEND_DATE="
  for /f "usebackq delims=" %%D in (`%SDE_PYTHON_CMD% tools\resolve_last_trading_day.py`) do set "RESEND_DATE=%%D"
  if not defined RESEND_DATE (
    echo Gagal menentukan hari trading terakhir.
    pause
    goto MENU
  )
  echo.
  echo Mengirim ulang Market Outlook trade date !RESEND_DATE! tanpa menjalankan engine...
  set "RESEND_ONLY=1"
)
if "%RESEND_ONLY%"=="1" (
  %SDE_PYTHON_CMD% tools\resend_daily_report.py --job market_outlook --trade-date !RESEND_DATE!
) else (
  if not defined ARGS goto MENU
  %SDE_PYTHON_CMD% run_sde_job.py %ARGS%
)
set "RC=%ERRORLEVEL%"
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --job market_outlook
echo.
echo Exit code: %RC%
set "ARGS="
pause
goto MENU
