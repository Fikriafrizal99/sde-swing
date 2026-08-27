@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0\.."
title SDE Swing - System and Status

call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo [FAILED] Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 9009
)

:MENU
cls
echo ================================================================
echo              FTJ Community - SYSTEM AND STATUS
echo ================================================================
echo.
echo [1] Cek Status Semua Job
echo [2] Test Telegram
echo [3] Configure / Cek Telegram Topic IDs
echo [4] Jalankan Test Validasi
echo [5] Buka Folder Output
echo [6] Final Watchlist Preview Existing ^(exact, delivery-only^)
echo [0] Kembali
echo.
set "CHOICE="
set /p "CHOICE=Pilih menu: "
if "%CHOICE%"=="1" goto STATUS
if "%CHOICE%"=="2" goto TELEGRAM_TEST
if "%CHOICE%"=="3" goto TELEGRAM_TOPICS
if "%CHOICE%"=="4" goto TESTS
if "%CHOICE%"=="5" goto OUTPUT
if "%CHOICE%"=="6" goto FW_PREVIEW
if "%CHOICE%"=="0" exit /b 0
goto MENU

:STATUS
call CHECK_SDE_STATUS.bat
goto MENU

:TELEGRAM_TEST
call maintenance\TEST_TELEGRAM.bat
goto MENU

:TELEGRAM_TOPICS
call maintenance\CONFIGURE_TELEGRAM_TOPICS.bat
goto MENU

:TESTS
cls
echo Menjalankan test validasi repository...
%SDE_PYTHON_CMD% -m pytest -q
set "RC=!ERRORLEVEL!"
echo.
if "!RC!"=="0" (echo [OK] Seluruh test lulus.) else (echo [FAILED] Test gagal. Exit code !RC!.)
pause
goto MENU

:OUTPUT
if not exist "data\output" mkdir "data\output"
start "" "data\output"
goto MENU

:FW_PREVIEW
cls
set "PREVIEW_DATE="
for /f "usebackq delims=" %%D in (`"%SDE_PYTHON_CMD% tools\resolve_last_trading_day.py" 2^>nul`) do set "PREVIEW_DATE=%%D"
if not defined PREVIEW_DATE (
  echo [FAILED] Gagal menentukan hari trading terakhir untuk Final Watchlist Preview Existing.
  pause
  goto MENU
)
echo Final Watchlist Preview Existing !PREVIEW_DATE! - exact archived delivery, tanpa engine/formatter/Telegram.
%SDE_PYTHON_CMD% -u tools\resend_final_watchlist_recovery.py --config config\pipeline.json --scheduler-config config\scheduler.json --trade-date !PREVIEW_DATE! --preview-only
set "RC=!ERRORLEVEL!"
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --job final_watchlist --delivery
echo.
echo Exit code: !RC!
pause
goto MENU
