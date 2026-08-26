@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title SDE Swing - Final Watchlist

:MENU
cls
echo ================================================================
echo                FTJ Community - FINAL WATCHLIST
echo ================================================================
echo.
echo [1] Jalankan Final Watchlist + kirim Telegram ^(maks 10 chart-card^)
echo [2] Preview / cek hasil trading terakhir ^(tanpa kirim^)
echo [3] Kirim ulang snapshot yang sudah dicek di [2]
echo [4] Cek status Final Watchlist
echo [0] Kembali
echo.
echo.
set "MODE="
set "STATUS_VIEW="
set /p "MODE=Pilih mode: "
if "%MODE%"=="0" exit /b 0
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD goto PYTHON_MISSING
if "%MODE%"=="4" goto STATUS_ONLY
if "%MODE%"=="3" goto RESEND
if "%MODE%"=="1" goto BROKER_PERIOD_NORMAL
if "%MODE%"=="2" goto PREVIEW_EXISTING
goto MENU

:BROKER_PERIOD_NORMAL
%SDE_PYTHON_CMD% -u tools\run_final_watchlist_entrypoint.py
set "RC=!ERRORLEVEL!"
goto STATUS

:PREVIEW_EXISTING
set "PREVIEW_DATE="
for /f "usebackq delims=" %%D in (`"%SDE_PYTHON_CMD% tools\resolve_last_trading_day.py" 2^>nul`) do set "PREVIEW_DATE=%%D"
if not defined PREVIEW_DATE goto PREVIEW_DATE_FAILED

echo.
echo Membuat preview format FINAL dari hasil trading terakhir !PREVIEW_DATE!...
echo Engine, scoring, decision, dan broker calculation TIDAK dijalankan ulang.
rem Legacy compatibility reference: tools\resend_final_watchlist.py --trade-date !PREVIEW_DATE! --preview-only
%SDE_PYTHON_CMD% -u tools\final_watchlist_snapshot.py --config config\pipeline.json --scheduler-config config\scheduler.json --trade-date !PREVIEW_DATE! --preview-only
set "RC=!ERRORLEVEL!"
set "STATUS_VIEW=--delivery"
goto STATUS

:RESEND
set "RESEND_DATE="
for /f "usebackq delims=" %%D in (`"%SDE_PYTHON_CMD% tools\resolve_last_trading_day.py" 2^>nul`) do set "RESEND_DATE=%%D"
if not defined RESEND_DATE goto RESEND_DATE_FAILED

echo.
echo Mengirim snapshot Final Watchlist yang terakhir dicek di [2] untuk !RESEND_DATE!...
rem Legacy compatibility reference: tools\resend_final_watchlist.py --trade-date !RESEND_DATE!
%SDE_PYTHON_CMD% -u tools\final_watchlist_snapshot.py --config config\pipeline.json --scheduler-config config\scheduler.json --trade-date !RESEND_DATE!
set "RC=!ERRORLEVEL!"
set "STATUS_VIEW=--delivery"
goto STATUS

:STATUS_ONLY
%SDE_PYTHON_CMD% tools\print_job_status.py --job final_watchlist
pause
goto MENU

:PREVIEW_DATE_FAILED
echo Gagal menentukan hari trading terakhir untuk Preview/Cek.
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
%SDE_PYTHON_CMD% tools\print_job_status.py --job final_watchlist !STATUS_VIEW!
echo.
echo Exit code: !RC!
pause
goto MENU
