@echo off
setlocal EnableExtensions EnableDelayedExpansion
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
echo Report portfolio TIDAK memakai thread Market/Post Market.
echo Jangan mengambil angka dari link/message Telegram secara manual.
echo Cara paling aman: kirim tepat REPORT TEST di topic Report,
echo lalu gunakan Auto-detect + Validate.
echo.
echo [1] Tampilkan Chat ID + Forum Topic IDs dari Telegram
echo [2] Auto-detect REPORT TEST + Validate + Simpan
echo [3] Set manual + Validate + Simpan
echo [4] Cek routing Report yang sudah tervalidasi
echo [5] Lihat nilai topic environment saat ini
echo [0] Kembali
echo.
set "CHOICE="
set /p "CHOICE=Pilih menu: "
if "%CHOICE%"=="1" goto SHOW
if "%CHOICE%"=="2" goto AUTO_REPORT
if "%CHOICE%"=="3" goto SET_REPORT
if "%CHOICE%"=="4" goto CHECK_ROUTE
if "%CHOICE%"=="5" goto STATUS
if "%CHOICE%"=="0" exit /b 0
goto MENU

:SHOW
cls
echo Kirim tepat REPORT TEST di topic Report terlebih dahulu.
echo Hanya gunakan thread ID dari TELEGRAM_CHAT_ID yang sama dengan SDE.
echo.
%SDE_PYTHON_CMD% -u modules\telegram\get_chat_id.py
pause
goto MENU

:AUTO_REPORT
cls
echo Kirim tepat REPORT TEST di topic Report sebelum melanjutkan.
echo.
set "REPORT_ID="
for /f "usebackq delims=" %%I in (`%SDE_PYTHON_CMD% -u tools\detect_telegram_report_topic.py --marker "REPORT TEST" --id-only 2^>nul`) do set "REPORT_ID=%%I"
if not defined REPORT_ID (
  echo [FAILED] REPORT TEST belum dapat dideteksi pada chat SDE.
  echo Jalankan menu [1] untuk melihat update Telegram yang terbaca.
  pause
  goto MENU
)
echo [DETECTED] message_thread_id=!REPORT_ID!
goto VALIDATE_AND_SAVE

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
goto VALIDATE_AND_SAVE

:VALIDATE_AND_SAVE
set "OLD_REPORT_ID=!TELEGRAM_THREAD_REPORT_ID!"
echo.
echo [VALIDATE] Mengirim pesan tes silent ke Report thread !REPORT_ID! lalu auto-delete...
%SDE_PYTHON_CMD% -u tools\check_telegram_report_route.py --thread-id "!REPORT_ID!" --definitive
set "VALIDATE_RC=!ERRORLEVEL!"
if not "!VALIDATE_RC!"=="0" (
  set "TELEGRAM_THREAD_REPORT_ID=!OLD_REPORT_ID!"
  echo.
  echo [FAILED] Thread !REPORT_ID! TIDAK disimpan karena sendMessage Telegram gagal.
  echo Kirim tepat REPORT TEST di topic Report lalu gunakan menu [2].
  pause
  goto MENU
)

setx TELEGRAM_THREAD_REPORT_ID "!REPORT_ID!" >nul
if errorlevel 1 (
  set "TELEGRAM_THREAD_REPORT_ID=!OLD_REPORT_ID!"
  echo [FAILED] Topic valid tetapi gagal menyimpan TELEGRAM_THREAD_REPORT_ID.
  pause
  goto MENU
)
set "TELEGRAM_THREAD_REPORT_ID=!REPORT_ID!"
echo.
echo [OK] TELEGRAM_THREAD_REPORT_ID=!REPORT_ID! tervalidasi definitif dan tersimpan.
pause
goto MENU

:CHECK_ROUTE
cls
%SDE_PYTHON_CMD% -u tools\check_telegram_report_route.py
pause
goto MENU

:STATUS
cls
echo TELEGRAM_THREAD_SIGNAL_ID = !TELEGRAM_THREAD_SIGNAL_ID!
echo TELEGRAM_THREAD_REPORT_ID = !TELEGRAM_THREAD_REPORT_ID!
echo TELEGRAM_THREAD_SYSTEM_ID = !TELEGRAM_THREAD_SYSTEM_ID!
echo.
echo Topic Report hanya dianggap siap jika menu [4] menampilkan [OK].
echo Nilai setx aktif otomatis pada launcher/terminal baru.
pause
goto MENU
