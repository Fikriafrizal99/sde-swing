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
set "PLAYWRIGHT_PY=modules\portfolio\stockbit_playwright_collector.py"
set "TASK_FILE=data\input\broker\BROKER_PORTFOLIO_BACKFILL_TASKS.csv"

:MENU
set "PLAYWRIGHT_STATUS=OFF"
for /f "usebackq delims=" %%S in (`"%SDE_PYTHON_CMD% -u "%PLAYWRIGHT_PY%" status --value" 2^>nul`) do set "PLAYWRIGHT_STATUS=%%S"
cls
echo ================================================================
echo             FTJ Community - BROKER PORTFOLIO AKTIF
echo ================================================================
echo Playwright Auto Collector : !PLAYWRIGHT_STATUS!
echo.
echo.
echo Posisi BARU : pertama kali diisi dari tanggal BUY sampai sesi selesai terakhir.
echo Posisi LAMA : berikutnya hanya tanggal yang MISSING dari sesi yang sudah selesai.
echo Posisi SOLD : otomatis tidak ikut karena statusnya sudah CLOSED.
echo.
echo [1] UPDATE HARIAN
echo [2] IMPORT HASIL ke database
echo [3] CEK STATUS histori broker
echo [4] REPAIR / backfill satu emiten
echo [5] PLAYWRIGHT ON / OFF
echo [6] SETUP / LOGIN PLAYWRIGHT
echo [9] Refresh ulang SEMUA histori OPEN
echo [0] Kembali
echo.
set "CHOICE="
set /p "CHOICE=Pilih menu: "

if "%CHOICE%"=="1" goto DAILY
if "%CHOICE%"=="2" goto IMPORT
if "%CHOICE%"=="3" goto STATUS
if "%CHOICE%"=="4" goto ONE
if "%CHOICE%"=="5" goto PLAYWRIGHT_TOGGLE
if "%CHOICE%"=="6" goto PLAYWRIGHT_SETUP
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
echo CSV hanya berisi tanggal missing sampai SESI BEI TERAKHIR YANG SELESAI.
echo Tanggal kalender baru tidak otomatis menjadi missing sebelum 16:30 WIB.
echo.
%SDE_PYTHON_CMD% -u %BROKER_DAILY_PY% --output "%TASK_FILE%" daily
set "RC=!ERRORLEVEL!"
if not "!RC!"=="0" (
  echo.
  echo [FAILED] Gagal menyiapkan update harian.
  pause
  goto MENU
)
set "TASK_COUNT="
for /f "usebackq delims=" %%C in (`"%SDE_PYTHON_CMD% -u "%PLAYWRIGHT_PY%" task-count --tasks "%TASK_FILE%"" 2^>nul`) do set "TASK_COUNT=%%C"
if not defined TASK_COUNT (
  echo.
  echo [FAILED] Task CSV tidak dapat diverifikasi. Gunakan workflow manual.
  pause
  goto MENU
)
if "!TASK_COUNT!"=="0" (
  echo.
  echo [SELESAI] Tidak ada task missing. Browser Playwright tidak dijalankan.
  pause
  goto MENU
)
echo.
if /I "!PLAYWRIGHT_STATUS!"=="ON" (
  echo [AUTO] Playwright Broker Collector - !TASK_COUNT! task
  echo.
  %SDE_PYTHON_CMD% -u "%PLAYWRIGHT_PY%" collect --tasks "%TASK_FILE%"
  set "RC=!ERRORLEVEL!"
  if "!RC!"=="0" (
    echo.
    echo Database BELUM diubah. Pilih [2] IMPORT HASIL ke database.
    pause
    goto MENU
  )
  echo.
  echo [PLAYWRIGHT FAILED] Database tidak diubah dan final CSV baru tidak dibuat.
  echo Gunakan [6] SETUP / LOGIN PLAYWRIGHT, atau [5] PLAYWRIGHT OFF.
  echo.
)
echo [ACTION MANUAL]
echo Playwright Auto Collector : !PLAYWRIGHT_STATUS!
echo.
echo   1. Buka Broker Summary salah satu saham di Stockbit.
echo   2. Tampermonkey: impor %TASK_FILE%
echo   3. Klik Mulai Backfill.
echo   4. Setelah CSV hasil ter-download, kembali ke menu [2] IMPORT HASIL.
pause
goto MENU

:IMPORT
cls
echo ================================================================
echo                  IMPORT HASIL CSV BROKER
echo ================================================================
echo Menerima CSV kompatibel dari Tampermonkey maupun Playwright.
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

:PLAYWRIGHT_TOGGLE
cls
echo ================================================================
echo                 PLAYWRIGHT ON / OFF
echo ================================================================
if /I "!PLAYWRIGHT_STATUS!"=="ON" (
  %SDE_PYTHON_CMD% -u "%PLAYWRIGHT_PY%" disable
) else (
  %SDE_PYTHON_CMD% -u "%PLAYWRIGHT_PY%" enable
)
pause
goto MENU

:PLAYWRIGHT_SETUP
cls
echo ================================================================
echo               SETUP / LOGIN PLAYWRIGHT
echo ================================================================
echo Setup memakai browser headed dan login Stockbit manual.
echo Tidak ada username, password, atau Trading PIN yang diminta oleh SDE.
echo Browser Chromium hanya di-install melalui menu setup ini.
echo.
%SDE_PYTHON_CMD% -u "%PLAYWRIGHT_PY%" setup
set "RC=!ERRORLEVEL!"
if not "!RC!"=="0" (
  echo.
  echo [FAILED] Setup/login belum valid. Workflow Tampermonkey tetap tersedia.
)
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
echo TO_DATE kosong = sesi BEI terakhir yang sudah selesai.
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
set /p "TO_DATE=Sampai YYYY-MM-DD [Enter=sesi selesai terakhir]: "
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
echo Target default tetap dibatasi sampai sesi BEI terakhir yang sudah selesai.
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
