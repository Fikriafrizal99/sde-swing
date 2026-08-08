@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0\.."
title SDE Swing - Daily Operations

call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo [FAILED] Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 9009
)

:MENU
cls
echo ================================================================
echo              SDE SWING - DAILY OPERATIONS
echo ================================================================
echo.
echo [1] Full Daily + Portfolio Management
echo [2] Full Daily Engine Only
echo [3] Cek Status Semua Job
echo [0] Kembali
echo.
set "CHOICE="
set /p "CHOICE=Pilih menu: "
if "%CHOICE%"=="1" goto FULL_WITH_PORTFOLIO
if "%CHOICE%"=="2" goto FULL_ENGINE_ONLY
if "%CHOICE%"=="3" goto STATUS
if "%CHOICE%"=="0" exit /b 0
goto MENU

:FULL_WITH_PORTFOLIO
cls
echo Menjalankan Full Daily V1.7.0 + Portfolio Management...
echo.
%SDE_PYTHON_CMD% -u run_sde_job_integrated.py --job full_manual --interactive-broker
set "RC=!ERRORLEVEL!"
if "!RC!"=="0" (
  echo.
  echo [NEXT] Full Daily sukses. Menjalankan Portfolio Management non-blocking...
  call maintenance\RUN_POSITION_MANAGEMENT.bat --non-blocking
) else (
  echo.
  echo [SKIPPED] Portfolio Management tidak dijalankan karena Full Daily exit code !RC!.
)
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --job full_manual
pause
goto MENU

:FULL_ENGINE_ONLY
cls
echo Menjalankan Full Daily V1.7.0 tanpa Portfolio Management...
echo.
%SDE_PYTHON_CMD% -u run_sde_job_integrated.py --job full_manual --interactive-broker
set "RC=!ERRORLEVEL!"
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --job full_manual
echo Exit code: !RC!
pause
goto MENU

:STATUS
call CHECK_SDE_STATUS.bat
goto MENU
