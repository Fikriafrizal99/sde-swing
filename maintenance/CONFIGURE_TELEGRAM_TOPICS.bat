@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
title SDE - Telegram Topic Configuration

call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo [FAILED] Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 9009
)

:MENU
cls
echo ================================================================
echo          SDE - TELEGRAM TOPIC CONFIGURATION
echo ================================================================
echo.
echo Report portfolio TIDAK lagi memakai thread Market/Post Market.
echo Routing Report menggunakan environment variable:
echo   TELEGRAM_THREAD_REPORT_ID
echo.
echo ID baru hanya disimpan jika Telegram mengonfirmasi topic tersebut valid
echo untuk bot + TELEGRAM_CHAT_ID yang dipakai SDE.
echo.
echo [1] Tampilkan Chat ID + Forum Topic IDs dari Telegram
echo [2] Set + Validasi TELEGRAM_THREAD_REPORT_ID
echo [3] Cek routing LIVE Report vs Market/Post
echo [4] Lihat nilai topic environment saat ini
echo [0] Kembali
echo.
set "CHOICE="
set /p "CHOICE=Pilih menu: "
if "%CHOICE%"=="1" goto SHOW
if "%CHOICE%"=="2" goto SET_REPORT
if "%CHOICE%"=="3" goto CHECK_ROUTE
if "%CHOICE%"=="4" goto STATUS
if "%CHOICE%"=="0" exit /b 0
goto MENU

:SHOW
cls
echo Kirim satu pesan seperti REPORT TEST di topic Report terlebih dahulu.
echo Hanya gunakan thread ID dari TELEGRAM_CHAT_ID yang sama dengan SDE.
echo.
%SDE_PYTHON_CMD% -u modules\telegram\get_chat_id.py
pause
goto MENU

:SET_REPORT
cls
set "REPORT_ID="
set /p "REPORT_ID=Masukkan message_thread_id topic Report: "
if not defined REPORT_ID (
  echo [FAILED] Thread ID wajib diisi.
  pause
  goto MENU
)
for /f "delims=0123456789" %%A in ("%REPORT_ID%") do (
  echo [FAILED] Thread ID harus berupa angka.
  pause
  goto MENU
)

set "OLD_REPORT_ID=%TELEGRAM_THREAD_REPORT_ID%"
set "TELEGRAM_THREAD_REPORT_ID=%REPORT_ID%"
echo.
echo [VALIDATE] Memeriksa thread %REPORT_ID% langsung ke Telegram...
%SDE_PYTHON_CMD% -u tools\check_telegram_report_route.py
set "VALIDATE_RC=%ERRORLEVEL%"
if not "%VALIDATE_RC%"=="0" (
  set "TELEGRAM_THREAD_REPORT_ID=%OLD_REPORT_ID%"
  echo.
  echo [FAILED] Thread %REPORT_ID% TIDAK disimpan karena tidak lolos live validation.
  echo Kirim REPORT TEST di topic Report lalu gunakan menu [1] untuk membaca ulang ID.
  pause
  goto MENU
)

setx TELEGRAM_THREAD_REPORT_ID "%REPORT_ID%" >nul
if errorlevel 1 (
  set "TELEGRAM_THREAD_REPORT_ID=%OLD_REPORT_ID%"
  echo [FAILED] Validasi berhasil tetapi gagal menyimpan TELEGRAM_THREAD_REPORT_ID.
  pause
  goto MENU
)
set "TELEGRAM_THREAD_REPORT_ID=%REPORT_ID%"
echo.
echo [OK] TELEGRAM_THREAD_REPORT_ID=%REPORT_ID% sudah LIVE VALID dan tersimpan.
pause
goto MENU

:CHECK_ROUTE
cls
%SDE_PYTHON_CMD% -u tools\check_telegram_report_route.py
pause
goto MENU

:STATUS
cls
echo TELEGRAM_THREAD_SIGNAL_ID = %TELEGRAM_THREAD_SIGNAL_ID%
echo TELEGRAM_THREAD_REPORT_ID = %TELEGRAM_THREAD_REPORT_ID%
echo TELEGRAM_THREAD_SYSTEM_ID = %TELEGRAM_THREAD_SYSTEM_ID%
echo.
echo Catatan: angka saja belum menjamin valid. Gunakan menu [3] untuk live validation.
echo Nilai setx aktif otomatis pada terminal/launcher baru.
pause
goto MENU
