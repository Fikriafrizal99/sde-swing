@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title SDE Swing - Post Market

REM Canonical Post Market payload/runtime is the market-first closing-session
REM builder. The shim keeps the integrated lifecycle while using that one path.
REM The underlying control remains compatible with run_sde_job_integrated.py.

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
echo [5] Normal + Post Market News - satu kali jalan
echo [6] Post Market News Only
echo [7] Preview Post Market News existing
echo [8] Force Send Post Market News existing
echo [0] Kembali
echo.
set "MODE="
set /p "MODE=Pilih mode: "
if "%MODE%"=="0" exit /b 0
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD goto PYTHON_MISSING

if "%MODE%"=="4" goto STATUS_ONLY
if "%MODE%"=="3" goto RESEND
if "%MODE%"=="5" goto NORMAL_WITH_NEWS
if "%MODE%"=="6" goto NEWS_ONLY
if "%MODE%"=="7" goto NEWS_PREVIEW
if "%MODE%"=="8" goto NEWS_FORCE_SEND

set "ARGS="
if "%MODE%"=="1" set "ARGS=--job post_market"
if "%MODE%"=="2" set "ARGS=--job post_market --preview-existing --no-telegram"
if not defined ARGS goto MENU
%SDE_PYTHON_CMD% -u run_sde_job_integrated_market_first.py %ARGS%
set "RC=!ERRORLEVEL!"
goto STATUS

:NORMAL_WITH_NEWS
%SDE_PYTHON_CMD% -u run_sde_job_integrated_market_first.py --job post_market
set "RC=!ERRORLEVEL!"
if "!RC!"=="0" (
  echo.
  echo [NEWS] Menjalankan Post Market News non-blocking...
  %SDE_PYTHON_CMD% -u modules\news\news_monitor_market_impact.py run --session post_market --send
  set "NEWS_RC=!ERRORLEVEL!"
  if not "!NEWS_RC!"=="0" echo [WARNING] Post Market News gagal/dilewati ^(exit !NEWS_RC!^). Post Market tetap SUCCESS.
) else (
  echo [SKIPPED] Post Market News tidak dijalankan karena Post Market exit code !RC!.
)
goto STATUS

:NEWS_ONLY
%SDE_PYTHON_CMD% -u modules\news\news_monitor_market_impact.py run --session post_market --send
set "NEWS_RC=!ERRORLEVEL!"
echo.
echo News exit code: !NEWS_RC!
pause
goto MENU

:NEWS_PREVIEW
%SDE_PYTHON_CMD% -u modules\news\news_monitor_market_impact.py preview --session post_market
pause
goto MENU

:NEWS_FORCE_SEND
%SDE_PYTHON_CMD% -u modules\news\news_monitor_market_impact.py send --session post_market --force
set "NEWS_RC=!ERRORLEVEL!"
echo.
echo News exit code: !NEWS_RC!
pause
goto MENU

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
