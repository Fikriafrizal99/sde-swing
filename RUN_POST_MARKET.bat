@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title SDE Swing - Post Market

REM Canonical Post Market payload/runtime is the market-first closing-session
REM builder. Weekend preview is artifact-only; recovery is explicit and runs
REM the same frozen engine against the last completed trading date.

:MENU
cls
echo ================================================================
echo                  SDE SWING - POST MARKET
echo ================================================================
echo.
echo [1] Normal - refresh teknikal dan kirim ringkasan ^(hari trading^)
echo [2] Preview existing - sesi trading terakhir, read-only
echo [3] Kirim ulang - delivery-only hasil hari trading terakhir
echo [4] Cek status Post Market
echo [5] Normal + Post Market News - satu kali jalan
echo [6] Post Market News Only
echo [7] Preview Post Market News existing
echo [8] Force Send Post Market News existing
echo [9] Recovery sesi terakhir terlewat - jalankan Post Market tanpa Telegram
echo [0] Kembali
echo.
set "MODE="
set /p "MODE=Pilih mode: "
if "%MODE%"=="0" exit /b 0
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD goto PYTHON_MISSING

if "%MODE%"=="4" goto STATUS_ONLY
if "%MODE%"=="3" goto RESEND
if "%MODE%"=="2" goto PREVIEW_EXISTING
if "%MODE%"=="5" goto NORMAL_WITH_NEWS
if "%MODE%"=="6" goto NEWS_ONLY
if "%MODE%"=="7" goto NEWS_PREVIEW
if "%MODE%"=="8" goto NEWS_FORCE_SEND
if "%MODE%"=="9" goto RECOVER_LAST_SESSION
if not "%MODE%"=="1" goto MENU

%SDE_PYTHON_CMD% -u run_sde_job_integrated_market_first.py --job post_market
set "RC=!ERRORLEVEL!"
goto STATUS

:PREVIEW_EXISTING
set "PREVIEW_DATE="
for /f "usebackq delims=" %%D in (`"%SDE_PYTHON_CMD% tools\resolve_last_trading_day.py" 2^>nul`) do set "PREVIEW_DATE=%%D"
if not defined PREVIEW_DATE goto PREVIEW_DATE_FAILED

echo.
echo Membuka preview Post Market trade date !PREVIEW_DATE! dari artifact existing...
%SDE_PYTHON_CMD% -u tools\resend_daily_report.py --job post_market --trade-date !PREVIEW_DATE! --preview-only
set "RC=!ERRORLEVEL!"
goto STATUS

:RECOVER_LAST_SESSION
set "RECOVERY_DATE="
for /f "usebackq delims=" %%D in (`"%SDE_PYTHON_CMD% tools\resolve_last_trading_day.py" 2^>nul`) do set "RECOVERY_DATE=%%D"
if not defined RECOVERY_DATE goto RECOVERY_DATE_FAILED

echo.
echo ================================================================
echo RECOVERY POST MARKET - !RECOVERY_DATE!
echo ================================================================
echo Mesin/scoring tidak diubah. Runtime yang sama dijalankan dengan trade-date
echo sesi terakhir yang sudah selesai dan Telegram dimatikan.
echo Setelah berhasil, gunakan Preview Existing atau Kirim Ulang jika diperlukan.
echo.
%SDE_PYTHON_CMD% -u run_sde_job_integrated_market_first.py --job post_market --trade-date !RECOVERY_DATE! --no-telegram
set "RC=!ERRORLEVEL!"
goto STATUS

:NORMAL_WITH_NEWS
%SDE_PYTHON_CMD% -u run_sde_job_integrated_market_first.py --job post_market
set "RC=!ERRORLEVEL!"
if "!RC!"=="0" (
  echo.
  echo [NEWS] Menjalankan Post Market News non-blocking...
  %SDE_PYTHON_CMD% -u modules\news\news_monitor_fresh_grouped.py run --session post_market --send
  set "NEWS_RC=!ERRORLEVEL!"
  if not "!NEWS_RC!"=="0" echo [WARNING] Post Market News gagal/dilewati ^(exit !NEWS_RC!^). Post Market tetap SUCCESS.
) else (
  echo [SKIPPED] Post Market News tidak dijalankan karena Post Market exit code !RC!.
)
goto STATUS

:NEWS_ONLY
%SDE_PYTHON_CMD% -u modules\news\news_monitor_fresh_grouped.py run --session post_market --send
set "NEWS_RC=!ERRORLEVEL!"
echo.
echo News exit code: !NEWS_RC!
pause
goto MENU

:NEWS_PREVIEW
%SDE_PYTHON_CMD% -u modules\news\news_monitor_fresh_grouped.py preview --session post_market
pause
goto MENU

:NEWS_FORCE_SEND
%SDE_PYTHON_CMD% -u modules\news\news_monitor_fresh_grouped.py send --session post_market --force
set "NEWS_RC=!ERRORLEVEL!"
echo.
echo News exit code: !NEWS_RC!
pause
goto MENU

:RESEND
set "RESEND_DATE="
for /f "usebackq delims=" %%D in (`"%SDE_PYTHON_CMD% tools\resolve_last_trading_day.py" 2^>nul`) do set "RESEND_DATE=%%D"
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

:PREVIEW_DATE_FAILED
echo Gagal menentukan hari trading terakhir untuk Preview Existing.
pause
goto MENU

:RECOVERY_DATE_FAILED
echo Gagal menentukan sesi trading terakhir untuk Recovery.
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
