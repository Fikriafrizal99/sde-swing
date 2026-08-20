@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scheduler\Check-SdeSchedulers.ps1"
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
