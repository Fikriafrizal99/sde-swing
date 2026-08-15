@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD exit /b 9009
REM Task Scheduler is non-interactive; scheduled Final Watchlist uses the
REM lifecycle-aware entrypoint so weekend/holiday execution resolves to the
REM latest completed IDX trading session before running the 1D Broker Period.
%SDE_PYTHON_CMD% -u tools\run_final_watchlist_entrypoint.py --period 1D %*
exit /b %ERRORLEVEL%
