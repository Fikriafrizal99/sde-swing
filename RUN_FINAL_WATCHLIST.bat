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
echo [1] Normal - Broker Summary 1D / 3D / 5D / Custom / Reuse
echo [2] Preview exact/recovery source - kunci source run
echo [3] Kirim exact preview terakhir ke Telegram
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
echo Membuka exact preview atau recovery source Final Watchlist pada !PREVIEW_DATE!...
%SDE_PYTHON_CMD% -u tools\resend_final_watchlist_recovery.py --trade-date !PREVIEW_DATE! --preview-only
set "RC=!ERRORLEVEL!"
set "STATUS_VIEW=--delivery"
goto STATUS

:RESEND
set "RESEND_DATE="
for /f "usebackq delims=" %%D in (`"%SDE_PYTHON_CMD% tools\resolve_last_trading_day.py" 2^>nul`) do set "RESEND_DATE=%%D"
if not defined RESEND_DATE goto RESEND_DATE_FAILED

echo.
echo Mengirim exact preview Final Watchlist yang terakhir disetujui untuk !RESEND_DATE! ke Telegram...
%SDE_PYTHON_CMD% -u tools\resend_final_watchlist_recovery.py --trade-date !RESEND_DATE!
set "RC=!ERRORLEVEL!"
set "STATUS_VIEW=--delivery"
goto STATUS

:STATUS_ONLY
%SDE_PYTHON_CMD% tools\print_job_status.py --job final_watchlist
pause
goto MENU

:PREVIEW_DATE_FAILED
echo Gagal menentukan hari trading terakhir untuk Preview Existing.
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