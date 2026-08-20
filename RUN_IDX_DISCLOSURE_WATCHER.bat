@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 9009
)
echo Starting IDX Disclosure Watcher...
%SDE_PYTHON_CMD% -u run_idx_disclosure_watcher.py --watch --telegram --transport playwright
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo IDX Disclosure Watcher stopped with exit code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
