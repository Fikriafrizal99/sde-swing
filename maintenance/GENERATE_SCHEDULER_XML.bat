@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD exit /b 9009

%SDE_PYTHON_CMD% generate_task_scheduler_xml.py
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo [OK] XML baru tersedia di scheduler\windows\generated
  echo [INFO] Hapus XML lama sebelum import dan gunakan file yang baru dibuat.
) else (
  echo [ERROR] Generate XML gagal. Exit code: %RC%
)
echo.
pause
exit /b %RC%
