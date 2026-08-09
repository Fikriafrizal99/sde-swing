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
echo [4] Cek status Market Outlook
echo [5] Normal + Morning News - satu kali jalan
echo [6] Morning News Only
echo [7] Preview Morning News existing
echo [8] Force Send Morning News existing
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
if "%MODE%"=="1" set "ARGS=--job market_outlook"
if "%MODE%"=="2" set "ARGS=--job market_outlook --preview-existing --no-telegram"
if not defined ARGS goto MENU
%SDE_PYTHON_CMD% -u run_sde_job_integrated.py %ARGS%
set "RC=!ERRORLEVEL!"
goto STATUS

:NORMAL_WITH_NEWS
%SDE_PYTHON_CMD% -u run_sde_job_integrated.py --job market_outlook
set "RC=!ERRORLEVEL!"
if "!RC!"=="0" (
  echo.
  echo [NEWS] Menjalankan Morning News non-blocking...
  %SDE_PYTHON_CMD% -u modules\news\news_monitor_market_impact.py run --session morning --send
  set "NEWS_RC=!ERRORLEVEL!"
  if not "!NEWS_RC!"=="0" echo [WARNING] Morning News gagal/dilewati ^(exit !NEWS_RC!^). Market Outlook tetap SUCCESS.
) else (
  echo [SKIPPED] Morning News tidak dijalankan karena Market Outlook exit code !RC!.
)
goto STATUS

:NEWS_ONLY
%SDE_PYTHON_CMD% -u modules\news\news_monitor_market_impact.py run --session morning --send
set "NEWS_RC=!ERRORLEVEL!"
echo.
echo News exit code: !NEWS_RC!
pause
goto MENU

:NEWS_PREVIEW
%SDE_PYTHON_CMD% -u modules\news\news_monitor_market_impact.py preview --session morning
pause
goto MENU

:NEWS_FORCE_SEND
%SDE_PYTHON_CMD% -u modules\news\news_monitor_market_impact.py send --session morning --force
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
echo Mengirim ulang Market Outlook trade date %RESEND_DATE% tanpa menjalankan engine...
%SDE_PYTHON_CMD% -u tools\resend_daily_report.py --job market_outlook --trade-date %RESEND_DATE%
set "RC=!ERRORLEVEL!"
goto STATUS

:STATUS_ONLY
%SDE_PYTHON_CMD% tools\print_job_status.py --job market_outlook
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
%SDE_PYTHON_CMD% tools\print_job_status.py --job market_outlook
echo.
echo Exit code: !RC!
pause
goto MENU
