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
echo               SDE SWING - SYSTEM AND STATUS
echo ================================================================
echo.
echo [1] Cek Status Semua Job
echo [2] Test Telegram
echo [3] Configure / Cek Telegram Topic IDs
echo [4] Jalankan Test Validasi
echo [5] Buka Folder Output
echo [6] Final Watchlist Preview Existing
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
%SDE_PYTHON_CMD% -u run_sde_job_integrated.py --job final_watchlist --preview-existing --no-telegram
set "RC=!ERRORLEVEL!"
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --job final_watchlist
pause
goto MENU
