@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0\.."
title SDE - Backfill Broker Portfolio

call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo [FAILED] Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 9009
)

set "BACKFILL_PY=modules\portfolio\broker_portfolio_backfill.py"
set "TASK_FILE=data\input\broker\BROKER_PORTFOLIO_BACKFILL_TASKS.csv"

:MENU
cls
echo ================================================================
echo          SDE - BACKFILL BROKER PORTFOLIO
echo ================================================================
echo Jalur ini TERPISAH dari Broker Summary Final Watchlist.
echo Database tujuan tetap: data\database\sde_swing_history.db
echo Tampermonkey: SDE Broker Portfolio Backfill v2.1+
echo.
echo [1] Siapkan backfill semua portfolio OPEN
echo [2] Siapkan backfill satu emiten / manual
echo [3] Import hasil Tampermonkey ke database
echo [4] Cek coverage histori broker portfolio
echo [5] Refresh ulang histori OPEN + nominal top broker
echo [0] Keluar
echo.
set "CHOICE="
set /p "CHOICE=Pilih menu: "

if "%CHOICE%"=="1" goto PREPARE_ALL
if "%CHOICE%"=="2" goto PREPARE_ONE
if "%CHOICE%"=="3" goto IMPORT
if "%CHOICE%"=="4" goto STATUS
if "%CHOICE%"=="5" goto PREPARE_FORCE
if "%CHOICE%"=="0" goto END

echo Pilihan tidak valid.
pause
goto MENU

:PREPARE_ALL
echo.
%SDE_PYTHON_CMD% -u %BACKFILL_PY% prepare --output "%TASK_FILE%"
set "RC=!ERRORLEVEL!"
if "!RC!"=="0" (
  echo.
  echo [NEXT]
  echo 1. Buka Stockbit Broker Summary satu saham.
  echo 2. Di Tampermonkey gunakan script: SDE Broker Portfolio Backfill v2.1+.
  echo 3. Impor file: %TASK_FILE%
  echo 4. Jalankan backfill lalu kembali ke menu [3] untuk import hasil.
)
pause
goto MENU

:PREPARE_ONE
echo.
set "SYMBOL="
set "FROM_DATE="
set "TO_DATE="
set /p "SYMBOL=Emiten (contoh TINS): "
if not defined SYMBOL (
  echo [FAILED] Emiten wajib diisi.
  pause
  goto MENU
)
echo Jika emiten sudah tercatat OPEN, FROM_DATE boleh kosong agar memakai actual buy_date.
set /p "FROM_DATE=FROM_DATE YYYY-MM-DD [kosong=buy_date OPEN]: "
set /p "TO_DATE=TO_DATE YYYY-MM-DD [kosong=hari trading terakhir]: "

set "DATE_ARGS="
if defined FROM_DATE set "DATE_ARGS=!DATE_ARGS! --from-date !FROM_DATE!"
if defined TO_DATE set "DATE_ARGS=!DATE_ARGS! --to-date !TO_DATE!"

%SDE_PYTHON_CMD% -u %BACKFILL_PY% prepare --symbol "%SYMBOL%" !DATE_ARGS! --output "%TASK_FILE%"
set "RC=!ERRORLEVEL!"
if "!RC!"=="0" (
  echo.
  echo [NEXT] Impor %TASK_FILE% ke Tampermonkey 'SDE Broker Portfolio Backfill v2.1+'.
  echo Script menerima emiten OPEN maupun emiten manual di luar Final Watchlist.
)
pause
goto MENU

:PREPARE_FORCE
echo.
echo Mode ini sengaja mengambil ulang tanggal yang SUDAH ada agar snapshot baru
 echo membawa nominal TOP BUYER/TOP SELLER untuk interpretasi portfolio.
echo Final Watchlist tetap tidak disentuh.
%SDE_PYTHON_CMD% -u %BACKFILL_PY% prepare --force --output "%TASK_FILE%"
set "RC=!ERRORLEVEL!"
if "!RC!"=="0" (
  echo.
  echo [NEXT] Impor %TASK_FILE% ke Tampermonkey v2.1+, jalankan sekali, lalu import hasil lewat menu [3].
)
pause
goto MENU

:IMPORT
echo.
echo File hasil harus bernama BROKER_PORTFOLIO_BACKFILL_SUMMARY_*.csv.
echo Kosongkan input agar sistem mengambil file backfill terbaru dari Downloads.
set "IMPORT_FILE="
set /p "IMPORT_FILE=Path file backfill [kosong=latest Downloads]: "
if defined IMPORT_FILE (
  %SDE_PYTHON_CMD% -u %BACKFILL_PY% import --file "%IMPORT_FILE%"
) else (
  %SDE_PYTHON_CMD% -u %BACKFILL_PY% import
)
set "RC=!ERRORLEVEL!"
if "!RC!"=="0" (
  echo [OK] Snapshot DAILY sudah diarsipkan ke database broker yang sama.
  echo Final Watchlist tidak dijalankan ulang.
) else (
  echo [FAILED] Import backfill gagal. Database tidak boleh diisi data cumulative/wrong-date.
)
pause
goto MENU

:STATUS
echo.
%SDE_PYTHON_CMD% -u %BACKFILL_PY% status
pause
goto MENU

:END
endlocal
exit /b 0