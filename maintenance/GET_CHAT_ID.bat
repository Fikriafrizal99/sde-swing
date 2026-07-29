@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD exit /b 9009
%SDE_PYTHON_CMD% modules\telegram\get_chat_id.py
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
