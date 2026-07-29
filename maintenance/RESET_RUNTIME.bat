@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
choice /C YN /N /M "Bersihkan status/log/cache/preview runtime? [Y/N] "
if errorlevel 2 exit /b 0
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD exit /b 9009
%SDE_PYTHON_CMD% tools\clean_runtime_artifacts.py --apply
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
