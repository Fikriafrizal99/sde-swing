@echo off
setlocal EnableExtensions EnableDelayedExpansion
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
echo [3] Kirim ulang - delivery-only hasil hari trading terakhir
echo [4] Cek status Post Market
echo [0] Kembali
echo.
set "MODE="
set /p "MODE=Pilih mode: "
if "%MODE%"=="0" exit /b 0
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD goto PYTHON_MISSING

if "%MODE%"=="4" goto STATUS_ONLY
if "%MODE%"=="3" goto RESEND

set "ARGS="
if "%MODE%"=="1" set "ARGS=--job post_market"
if "%MODE%"=="2" set "ARGS=--job post_market --preview-existing --no-telegram"
if not defined ARGS goto MENU
%SDE_PYTHON_CMD% -u run_sde_job_integrated.py %ARGS%
set "RC=!ERRORLEVEL!"
goto STATUS

:RESEND
set "RESEND_DATE="
for /f "delims=" %%D in ('%SDE_PYTHON_CMD% tools\resolve_last_trading_day.py 2^>nul') do set "RESEND_DATE=%%D"
if not defined RESEND_DATE goto RESEND_DATE_FAILED

echo.
echo Mengirim ulang Post Market trade date %RESEND_DATE% tanpa menjalankan engine...
%SDE_PYTHON_CMD% -u tools\resend_daily_report.py --job post_market --trade-date %RESEND_DATE%
set "RC=!ERRORLEVEL!"
goto STATUS

:STATUS_ONLY
%SDE_PYTHON_CMD% tools\print_job_status.py --job post_market
pause
goto MENU

:RESEND_DATE_FAILED
echo Gagal menentukan hari trading terakhir.
pause
goto MENU

:PYTHON_MISSING
echo Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
pause
goto MENU

:STATUS
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --job post_market
echo.
echo Exit code: !RC!
pause
goto MENU
