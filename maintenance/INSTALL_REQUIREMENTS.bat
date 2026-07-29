@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo Python tidak ditemukan. Install Python 3 terlebih dahulu.
  pause
  exit /b 9009
)
%SDE_PYTHON_CMD% -m pip install -r requirements.txt
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
