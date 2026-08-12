@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0\.."
title SDE - Update Broker Portfolio

call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo [FAILED] Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 9009
)

set "BROKER_DAILY_PY=modules\portfolio\portfolio_broker_daily.py"
set "TASK_FILE=data\input\broker\BROKER_PORTFOLIO_BACKFILL_TASKS.csv"

:MENU
cls
echo ================================================================
echo             SDE - BROKER PORTFOLIO AKTIF
echo ================================================================
echo Mode normal HANYA mengambil tanggal broker yang belum ada di database.
echo.
echo Posisi BARU : pertama kali diisi dari tanggal BUY sampai trading day terakhir.
echo Posisi LAMA : berikutnya hanya tanggal yang MISSING / hari berjalan.
echo Posisi SOLD : otomatis tidak ikut karena statusnya sudah CLOSED.
echo.
echo [1] UPDATE HARIAN  ^(disarankan^)
echo [2] IMPORT HASIL Tampermonkey ke database
echo [3] CEK STATUS histori broker
echo [4] REPAIR / backfill satu emiten
echo [9] ADVANCED - refresh ulang SEMUA histori OPEN
echo [0] Kembali
echo.
set "CHOICE="
set /p "CHOICE=Pilih menu: "

if "%CHOICE%"=="1" goto DAILY
if "%CHOICE%"=="2" goto IMPORT
if "%CHOICE%"=="3" goto STATUS
if "%CHOICE%"=="4" goto ONE
if "%CHOICE%"=="9" goto REFRESH_ALL
if "%CHOICE%"=="0" goto END

echo Pilihan tidak valid.
pause
goto MENU

:DAILY
cls
echo ================================================================
echo                 UPDATE BROKER HARIAN
echo ================================================================
echo Sistem mengecek portfolio OPEN + histori yang SUDAH ada di database.
echo CSV yang dibuat hanya berisi tanggal yang masih MISSING.
echo.
%SDE_PYTHON_CMD% -u %BROKER_DAILY_PY% --output "%TASK_FILE%" daily
set "RC=!ERRORLEVEL!"
if not "!RC!"=="0" (
  echo.
  echo [FAILED] Gagal menyiapkan update harian.
  pause
  goto MENU
)
echo.
echo Jika output di atas bertuliskan [ACTION]:
echo   1. Buka Broker Summary salah satu saham di Stockbit.
echo   2. Tampermonkey: impor %TASK_FILE%
echo   3. Klik Mulai Backfill.
echo   4. Setelah CSV hasil ter-download, kembali ke menu [2] IMPORT HASIL.
echo.
echo Jika bertuliskan [SELESAI], tidak perlu menjalankan Tampermonkey.
pause
goto MENU

:IMPORT
cls
echo ================================================================
echo                  IMPORT HASIL TAMPERMONKEY
echo ================================================================
echo Setelah import berhasil, task CSV otomatis dibangun ulang dari database.
echo Jadi tanggal lama dan posisi CLOSED tidak tertinggal di CSV berikutnya.
echo.
echo Kosongkan input untuk mengambil file hasil terbaru dari folder Downloads.
set "IMPORT_FILE="
set /p "IMPORT_FILE=Path BROKER_PORTFOLIO_BACKFILL_SUMMARY_*.csv [Enter=latest]: "
if defined IMPORT_FILE (
  %SDE_PYTHON_CMD% -u %BROKER_DAILY_PY% --output "%TASK_FILE%" import --file "%IMPORT_FILE%"
) else (
  %SDE_PYTHON_CMD% -u %BROKER_DAILY_PY% --output "%TASK_FILE%" import
)
set "RC=!ERRORLEVEL!"
if not "!RC!"=="0" echo [FAILED] Import gagal. Database tidak diubah dengan data yang tidak valid.
pause
goto MENU

:STATUS
cls
%SDE_PYTHON_CMD% -u %BROKER_DAILY_PY% status
pause
goto MENU

:ONE
cls
echo ================================================================
echo                   REPAIR SATU EMITEN
echo ================================================================
echo Untuk posisi OPEN, FROM_DATE boleh kosong supaya memakai actual BUY date.
echo Sistem tetap melewati tanggal yang sudah tersedia di database.
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
set /p "FROM_DATE=Mulai YYYY-MM-DD [Enter=BUY date OPEN]: "
set /p "TO_DATE=Sampai YYYY-MM-DD [Enter=trading day terakhir]: "
set "ARGS=--symbol !SYMBOL!"
if defined FROM_DATE set "ARGS=!ARGS! --from-date !FROM_DATE!"
if defined TO_DATE set "ARGS=!ARGS! --to-date !TO_DATE!"
%SDE_PYTHON_CMD% -u %BROKER_DAILY_PY% --output "%TASK_FILE%" one !ARGS!
pause
goto MENU

:REFRESH_ALL
cls
echo ================================================================
echo             ADVANCED - REFRESH SEMUA HISTORI OPEN
echo ================================================================
echo PERINGATAN: mode ini sengaja meminta ulang tanggal yang SUDAH ada di DB.
echo Jangan gunakan untuk update harian biasa.
echo.
set "CONFIRM="
set /p "CONFIRM=Ketik REFRESH untuk lanjut: "
if /I not "!CONFIRM!"=="REFRESH" goto MENU
%SDE_PYTHON_CMD% -u %BROKER_DAILY_PY% --output "%TASK_FILE%" refresh-all
pause
goto MENU

:END
endlocal
exit /b 0
