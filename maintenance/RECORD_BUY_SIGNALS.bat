@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0\.."
title SDE - Record BUY Signals

call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 9009
)

echo ================================================================
echo       SDE - REGISTER SEMUA KEPUTUSAN BUY MESIN
echo ================================================================
echo Tidak ada Yahoo refresh atau engine scan baru pada maintenance ini.
echo Source of truth: SQLite signal_outcome_ledger.
echo.

%SDE_PYTHON_CMD% -u modules\analytics\outcome_tracker.py sync ^
  --db data\database\sde_swing_history.db ^
  --historical-dir data\output\historical\by_symbol ^
  --output-dir data\output\analytics\performance ^
  --decisions data\output\decision\FINAL_DECISION_V3.csv ^
  --entry-plans data\output\exit\ENTRY_PLANS.csv ^
  --bootstrap-db
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [OK] BUY signal ledger selesai diperbarui.
  echo File utama: data\output\analytics\performance\SIGNAL_OUTCOME_LEDGER.csv
  echo Active:     data\output\analytics\performance\ACTIVE_RECOMMENDATIONS.csv
  echo Events:     data\output\analytics\performance\LIFECYCLE_EVENTS.csv
) else (
  echo [FAILED] Register BUY signal gagal. Exit code %RC%.
)
pause
exit /b %RC%
