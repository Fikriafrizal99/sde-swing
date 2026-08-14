@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD exit /b 9009
REM Task Scheduler is non-interactive; scheduled Final Watchlist uses the
REM current-day 1D Broker Period through the same Bridge as the manual flow.
%SDE_PYTHON_CMD% -u tools\run_final_watchlist_broker_period.py --period 1D %*
exit /b %ERRORLEVEL%
