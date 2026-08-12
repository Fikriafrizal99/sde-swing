@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0\.."
title SDE Swing - Portfolio Operations
rem Legacy label kept for compatibility checks: Backfill Broker Portfolio

:MENU
cls
echo ================================================================
echo             SDE SWING - PORTFOLIO OPERATIONS
echo ================================================================
echo.
echo [1] Register Semua BUY Mesin
echo [2] Maintain Portfolio Aktual
echo [3] Analisa Portfolio Aktif
echo [4] Update Broker Harian Portfolio
echo [5] Configure / Cek Topic Telegram Report
echo [6] Performance ^& Evaluation
echo [0] Kembali
echo.
set "CHOICE="
set /p "CHOICE=Pilih menu: "
if "%CHOICE%"=="1" (
  call maintenance\RECORD_BUY_SIGNALS.bat
  goto MENU
)
if "%CHOICE%"=="2" (
  call maintenance\RECORD_PORTFOLIO_BUY.bat
  goto MENU
)
if "%CHOICE%"=="3" (
  call maintenance\RUN_POSITION_MANAGEMENT.bat
  goto MENU
)
if "%CHOICE%"=="4" (
  call maintenance\BACKFILL_PORTFOLIO_BROKER.bat
  goto MENU
)
if "%CHOICE%"=="5" (
  call maintenance\CONFIGURE_TELEGRAM_TOPICS.bat
  goto MENU
)
if "%CHOICE%"=="6" (
  call maintenance\PERFORMANCE_MENU.bat
  goto MENU
)
if "%CHOICE%"=="0" exit /b 0
goto MENU
