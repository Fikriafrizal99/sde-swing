@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title SDE Swing V1.7.0 Multi-Source - Control Center

set "SDE_ROOT=%CD%"
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo.
  echo [ERROR] Python tidak ditemukan.
  echo Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 9009
)

set "PYTHON_VERSION=UNKNOWN"
for /f "usebackq delims=" %%V in (`"%SDE_PYTHON_CMD% --version" 2^>^&1`) do set "PYTHON_VERSION=%%V"
set "GIT_BRANCH=UNKNOWN"
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "GIT_BRANCH=%%B"

:MENU
cls
echo ================================================================
echo          SDE SWING V1.7.0 MULTI-SOURCE - CONTROL CENTER
echo ================================================================
echo Root   : %SDE_ROOT%
echo Branch : !GIT_BRANCH!
echo Python : !PYTHON_VERSION!
echo.
echo [1] Daily Operations
echo [2] Market Outlook
echo [3] Post Market
echo [4] Broker Operations
echo [5] Final Watchlist
echo [6] Portfolio Operations
echo [7] Performance ^& Evaluation
echo [8] System ^& Status
echo [9] Maintenance
echo [0] Keluar
echo.
set "MENU_CHOICE="
set /p "MENU_CHOICE=Pilih menu: "

if "%MENU_CHOICE%"=="1" (
  call maintenance\DAILY_OPERATIONS_MENU.bat
  goto MENU
)
if "%MENU_CHOICE%"=="2" (
  call RUN_MARKET_OUTLOOK.bat
  goto MENU
)
if "%MENU_CHOICE%"=="3" (
  call RUN_POST_MARKET.bat
  goto MENU
)
if "%MENU_CHOICE%"=="4" (
  call maintenance\BROKER_MENU.bat
  goto MENU
)
if "%MENU_CHOICE%"=="5" (
  call RUN_FINAL_WATCHLIST.bat
  goto MENU
)
if "%MENU_CHOICE%"=="6" (
  call maintenance\PORTFOLIO_MENU.bat
  goto MENU
)
if "%MENU_CHOICE%"=="7" (
  call maintenance\PERFORMANCE_MENU.bat
  goto MENU
)
if "%MENU_CHOICE%"=="8" (
  call maintenance\SYSTEM_MENU.bat
  goto MENU
)
if "%MENU_CHOICE%"=="9" (
  call maintenance\MAINTENANCE_MENU.bat
  goto MENU
)
if "%MENU_CHOICE%"=="0" exit /b 0

echo.
echo Pilihan tidak valid.
pause
goto MENU
