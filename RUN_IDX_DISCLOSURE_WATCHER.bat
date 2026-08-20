@echo off
setlocal
cd /d "%~dp0"
echo Starting IDX Disclosure Watcher...
python run_idx_disclosure_watcher.py --watch --telegram --transport playwright
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" (
  echo.
  echo IDX Disclosure Watcher stopped with exit code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
