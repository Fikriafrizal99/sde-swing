@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title SDE Swing V1.6.1 - Control Panel

:MENU
cls
echo ================================================================
echo             SDE SWING V1.6.1 - CONTROL PANEL
echo ================================================================
echo.
echo [1] Market Outlook
echo [2] Post Market
echo [3] Final Watchlist
echo [4] Full Manual Pipeline
echo [5] Cek Status Semua Job
echo [6] Test Telegram
echo [7] Performance ^& Evaluation
echo [8] Maintenance
echo [0] Keluar
echo.
set "MENU_CHOICE="
set /p "MENU_CHOICE=Pilih menu: "

if "%MENU_CHOICE%"=="1" (
  call RUN_MARKET_OUTLOOK.bat
  goto MENU
)
if "%MENU_CHOICE%"=="2" (
  call RUN_POST_MARKET.bat
  goto MENU
)
if "%MENU_CHOICE%"=="3" (
  call RUN_FINAL_WATCHLIST.bat
  goto MENU
)
if "%MENU_CHOICE%"=="4" goto FULL_PIPELINE
if "%MENU_CHOICE%"=="5" (
  call CHECK_SDE_STATUS.bat
  goto MENU
)
if "%MENU_CHOICE%"=="6" (
  call maintenance\TEST_TELEGRAM.bat
  goto MENU
)
if "%MENU_CHOICE%"=="7" (
  call maintenance\PERFORMANCE_MENU.bat
  goto MENU
)
if "%MENU_CHOICE%"=="8" (
  call maintenance\MAINTENANCE_MENU.bat
  goto MENU
)
if "%MENU_CHOICE%"=="0" exit /b 0
goto MENU

:FULL_PIPELINE
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  goto MENU
)
echo.
echo Menjalankan full manual pipeline...
%SDE_PYTHON_CMD% run_sde_job.py --job full_manual
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo FULL PIPELINE GAGAL. Exit code %RC%.
) else (
  echo FULL PIPELINE SELESAI.
)
pause
goto MENU
