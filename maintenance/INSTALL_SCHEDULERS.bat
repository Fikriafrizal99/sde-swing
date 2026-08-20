@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
echo Installing/updating SDE Swing Windows schedulers for the current user...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scheduler\Install-SdeSchedulers.ps1"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [OK] Scheduler installation/update completed.
  echo [INFO] Run maintenance\CHECK_SCHEDULERS.bat to verify status.
) else (
  echo [ERROR] Scheduler installation failed. Exit code: %RC%
)
echo.
pause
exit /b %RC%
