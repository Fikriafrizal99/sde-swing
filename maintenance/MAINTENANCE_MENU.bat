@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."

:MENU
cls
echo ================================================================
echo                 FTJ Community - MAINTENANCE
echo ================================================================
echo [1] Install requirements
echo [2] Reset runtime
echo [3] Verify manifest
echo [4] Generate Task Scheduler XML
echo [5] Release validation
echo [6] Get Telegram Chat ID
echo [7] Repair Yahoo/YFinance runtime
echo [0] Kembali
echo.
set "M="
set /p "M=Pilih menu: "

if "%M%"=="1" (
  call maintenance\INSTALL_REQUIREMENTS.bat
  goto MENU
)
if "%M%"=="2" (
  call maintenance\RESET_RUNTIME.bat
  goto MENU
)
if "%M%"=="3" (
  call maintenance\VERIFY_MANIFEST.bat
  goto MENU
)
if "%M%"=="4" (
  call maintenance\GENERATE_SCHEDULER_XML.bat
  goto MENU
)
if "%M%"=="5" (
  call maintenance\RELEASE_VALIDATION.bat
  goto MENU
)
if "%M%"=="6" (
  call maintenance\GET_CHAT_ID.bat
  goto MENU
)
if "%M%"=="7" (
  call maintenance\REPAIR_YAHOO_RUNTIME.bat
  goto MENU
)
if "%M%"=="0" exit /b 0
goto MENU
