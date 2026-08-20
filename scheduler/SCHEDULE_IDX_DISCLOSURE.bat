@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD exit /b 9009
%SDE_PYTHON_CMD% -u run_idx_disclosure_watcher.py --watch --telegram --transport playwright %*
exit /b %ERRORLEVEL%
