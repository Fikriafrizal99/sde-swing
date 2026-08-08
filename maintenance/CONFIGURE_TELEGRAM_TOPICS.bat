@echo off
set EnableExtensions EnableDelayedExpansion
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

echo [1] Tampilkan Chat ID + Forum Topic IDs dari Telegram
echo [2] Set TELEGRAM_THREAD_REPORT_ID
echo [3] Cek routing efektif Report vs Market/Post
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
echo Setelah itu hasil di bawah akan menampilkan message_thread_id.
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
for /f "delims=0123456789" %%A in ("!REPORT_ID!") do (
  echo [FAILED] Thread ID harus berupa angka.
  pause
  goto MENU
)
setx TELEGRAM_THREAD_REPORT_ID "!REPORT_ID!" >nul
if errorlevel 1 (
  echo [FAILED] Gagal menyimpan TELEGRAM_THREAD_REPORT_ID.
  pause
  goto MENU
)
set "TELEGRAM_THREAD_REPORT_ID=!REPORT_ID!"
echo [OK] TELEGRAM_THREAD_REPORT_ID=!REPORT_ID! tersimpan dan aktif untuk sesi RUN_SDE ini.
echo.
%SDE_PYTHON_CMD% -u tools\check_telegram_report_route.py
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
echo Nilai setx juga akan aktif otomatis pada terminal/launcher baru.
pause
goto MENU
