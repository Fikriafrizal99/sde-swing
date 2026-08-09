@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0\.."
title SDE Swing - Performance and Evaluation
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 1
)

:MENU
cls
echo ================================================================
echo          SDE SWING - PERFORMANCE AND EVALUATION
echo ================================================================
echo.
echo [1] Update semua outcome dan win rate
echo [2] Lihat performa keseluruhan
echo [3] Evaluasi berdasarkan setup
echo [4] Evaluasi berdasarkan jenis sinyal
echo [5] Evaluasi berdasarkan broker confidence
echo [6] Evaluasi berdasarkan market regime
echo [7] Lihat Signal Outcome Ledger
echo [8] Kirim laporan performance ke Telegram
echo [9] Register semua keputusan BUY mesin
echo [10] Maintain portfolio aktual
echo [11] Kirim lifecycle digest (status material)
echo [12] Kirim active recommendations ke Telegram
echo [13] Preview lifecycle digest terbaru (read-only)
echo [0] Kembali
echo.
set "PERF_CHOICE="
set /p "PERF_CHOICE=Pilih menu: "

if "%PERF_CHOICE%"=="1" goto UPDATE
if "%PERF_CHOICE%"=="2" goto SHOW_OVERALL
if "%PERF_CHOICE%"=="3" goto SHOW_SETUP
if "%PERF_CHOICE%"=="4" goto SHOW_SIGNAL
if "%PERF_CHOICE%"=="5" goto SHOW_BROKER
if "%PERF_CHOICE%"=="6" goto SHOW_REGIME
if "%PERF_CHOICE%"=="7" goto SHOW_LEDGER
if "%PERF_CHOICE%"=="8" goto SEND_TELEGRAM
if "%PERF_CHOICE%"=="9" goto REGISTER_BUY
if "%PERF_CHOICE%"=="10" goto PORTFOLIO
if "%PERF_CHOICE%"=="11" goto SEND_LIFECYCLE
if "%PERF_CHOICE%"=="12" goto SEND_ACTIVE
if "%PERF_CHOICE%"=="13" goto PREVIEW_LIFECYCLE
if "%PERF_CHOICE%"=="0" exit /b 0
goto MENU

:UPDATE
cls
echo [1/3] Membaca histori rekomendasi...
echo [2/3] Mengecek trigger, TP, SL, dan max hold...
echo [3/3] Membuat laporan performance...
%SDE_PYTHON_CMD% modules\analytics\outcome_tracker.py sync --bootstrap-db --decisions data\output\decision\FINAL_DECISION_V3.csv --entry-plans data\output\exit\ENTRY_PLANS.csv
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo Update outcome gagal. Exit code %RC%.
pause
goto MENU

:SHOW_OVERALL
cls
%SDE_PYTHON_CMD% modules\analytics\outcome_tracker.py show --section overall
pause
goto MENU

:SHOW_SETUP
cls
%SDE_PYTHON_CMD% modules\analytics\outcome_tracker.py show --section setup
pause
goto MENU

:SHOW_SIGNAL
cls
%SDE_PYTHON_CMD% modules\analytics\outcome_tracker.py show --section signal
pause
goto MENU

:SHOW_BROKER
cls
%SDE_PYTHON_CMD% modules\analytics\outcome_tracker.py show --section broker
pause
goto MENU

:SHOW_REGIME
cls
%SDE_PYTHON_CMD% modules\analytics\outcome_tracker.py show --section regime
pause
goto MENU

:SHOW_LEDGER
cls
%SDE_PYTHON_CMD% modules\analytics\outcome_tracker.py show --section ledger
pause
goto MENU

:SEND_TELEGRAM
cls
echo Laporan akan diperbarui sebelum dikirim.
%SDE_PYTHON_CMD% modules\analytics\outcome_tracker.py sync --bootstrap-db --decisions data\output\decision\FINAL_DECISION_V3.csv --entry-plans data\output\exit\ENTRY_PLANS.csv
if errorlevel 1 (
  echo Update outcome gagal. Pengiriman dibatalkan.
  pause
  goto MENU
)
set "CONFIRM_SEND="
set /p "CONFIRM_SEND=Kirim laporan performance ke Telegram? [Y/N]: "
if /I not "%CONFIRM_SEND%"=="Y" goto MENU
%SDE_PYTHON_CMD% modules\analytics\outcome_tracker.py telegram
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo Pengiriman gagal. Exit code %RC%.
pause
goto MENU

:REGISTER_BUY
call maintenance\RECORD_BUY_SIGNALS.bat
goto MENU

:PORTFOLIO
call maintenance\RECORD_PORTFOLIO_BUY.bat
goto MENU

:SEND_LIFECYCLE
cls
echo Lifecycle digest akan diperbarui dari SQLite dan dikirim terpisah.
echo Tidak ada Yahoo refresh atau engine scan pada menu ini.
%SDE_PYTHON_CMD% modules\analytics\outcome_tracker.py sync --bootstrap-db
if errorlevel 1 (
  echo Update lifecycle gagal. Pengiriman dibatalkan.
  pause
  goto MENU
)
echo.
set "CONFIRM_LIFECYCLE="
set /p "CONFIRM_LIFECYCLE=Kirim lifecycle digest material ke Telegram? [Y/N]: "
if /I not "%CONFIRM_LIFECYCLE%"=="Y" goto MENU
%SDE_PYTHON_CMD% modules\analytics\outcome_tracker.py lifecycle-telegram
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo Pengiriman lifecycle gagal. Exit code %RC%.
pause
goto MENU

:SEND_ACTIVE
cls
echo Active recommendations akan diperbarui dari SQLite dan dikirim terpisah.
echo Tidak ada Yahoo refresh atau engine scan pada menu ini.
%SDE_PYTHON_CMD% modules\analytics\outcome_tracker.py sync --bootstrap-db
if errorlevel 1 (
  echo Update active recommendations gagal. Pengiriman dibatalkan.
  pause
  goto MENU
)
echo.
set "CONFIRM_ACTIVE="
set /p "CONFIRM_ACTIVE=Kirim active recommendations ke Telegram? [Y/N]: "
if /I not "%CONFIRM_ACTIVE%"=="Y" goto MENU
%SDE_PYTHON_CMD% tools\send_active_recommendations.py
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo Pengiriman active recommendations gagal. Exit code %RC%.
pause
goto MENU

:PREVIEW_LIFECYCLE
cls
echo Preview lifecycle digest terbaru dari SQLite.
echo READ-ONLY: tidak refresh Yahoo, tidak scan engine, tidak kirim Telegram,
echo dan tidak mengubah telegram_notified_at.
echo.
%SDE_PYTHON_CMD% tools\preview_lifecycle_digest.py --limit 8
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo Preview lifecycle gagal. Exit code %RC%.
pause
goto MENU
