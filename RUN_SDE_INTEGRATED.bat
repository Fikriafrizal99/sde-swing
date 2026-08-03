@echo off
setlocal
cd /d "%~dp0"

if "%~1"=="" (
  echo Usage: RUN_SDE_INTEGRATED.bat market_outlook^|post_market^|broker_summary^|broker_multi_day^|final_watchlist^|full_manual
  exit /b 2
)

python -u run_sde_job_integrated.py --job %1
set EXIT_CODE=%ERRORLEVEL%

if not "%EXIT_CODE%"=="0" (
  echo.
  echo SDE integrated job gagal dengan exit code %EXIT_CODE%.
) else (
  echo.
  echo SDE integrated job selesai.
)

exit /b %EXIT_CODE%
