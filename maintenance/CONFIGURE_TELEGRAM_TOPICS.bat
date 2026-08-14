@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0\.."
title SDE - Telegram and News Settings

call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo [FAILED] Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 9009
)

:MENU
cls
echo ================================================================
echo          SDE - TELEGRAM ^& NEWS SETTINGS
echo ================================================================
echo.
echo Credential disimpan lokal dan tidak dicommit ke Git.
echo Setiap topic divalidasi dengan pesan silent lalu auto-delete.
echo.
echo [1] Set / Update Telegram Bot Token
echo [2] Set / Update Telegram Chat ID
echo [3] Validate Bot + Chat
echo [4] Configure Market / Post Market Topic
echo [5] Configure Final Watchlist Topic
echo [6] Configure Signal Detail Topic
echo [7] Configure Report / Portfolio / Performance Topic
echo [8] Configure System Topic
echo [9] Configure News Topic ^(1451^)
echo [10] Lihat Status Semua Setting
echo [11] Test Semua Telegram Route
echo [12] Set / Update Brave Search API Key
echo [13] Test Brave Search API
echo [14] Auto-detect REPORT TEST + Validate + Simpan
echo [15] Tampilkan Chat ID + Forum Topic IDs dari Telegram
echo [0] Kembali
echo.
set "CHOICE="
set /p "CHOICE=Pilih menu: "
if "%CHOICE%"=="1" goto SET_TOKEN
if "%CHOICE%"=="2" goto SET_CHAT
if "%CHOICE%"=="3" goto VALIDATE_CREDENTIALS
if "%CHOICE%"=="4" goto TOPIC_MARKET
if "%CHOICE%"=="5" goto TOPIC_FINAL
if "%CHOICE%"=="6" goto TOPIC_SIGNAL
if "%CHOICE%"=="7" goto TOPIC_REPORT
if "%CHOICE%"=="8" goto TOPIC_SYSTEM
if "%CHOICE%"=="9" goto TOPIC_NEWS
if "%CHOICE%"=="10" goto STATUS
if "%CHOICE%"=="11" goto TEST_ALL
if "%CHOICE%"=="12" goto SET_BRAVE
if "%CHOICE%"=="13" goto TEST_BRAVE
if "%CHOICE%"=="14" goto AUTO_REPORT
if "%CHOICE%"=="15" goto SHOW
if "%CHOICE%"=="0" exit /b 0
goto MENU

:SET_TOKEN
cls
%SDE_PYTHON_CMD% -u tools\telegram_settings.py set-token
pause
goto MENU

:SET_CHAT
cls
%SDE_PYTHON_CMD% -u tools\telegram_settings.py set-chat
pause
goto MENU

:VALIDATE_CREDENTIALS
cls
%SDE_PYTHON_CMD% -u tools\telegram_settings.py validate-credentials
pause
goto MENU

:TOPIC_MARKET
cls
%SDE_PYTHON_CMD% -u tools\telegram_settings.py set-topic --name market
pause
goto MENU

:TOPIC_FINAL
cls
%SDE_PYTHON_CMD% -u tools\telegram_settings.py set-topic --name final_watchlist
pause
goto MENU

:TOPIC_SIGNAL
cls
%SDE_PYTHON_CMD% -u tools\telegram_settings.py set-topic --name signal_detail
pause
goto MENU

:TOPIC_REPORT
cls
%SDE_PYTHON_CMD% -u tools\telegram_settings.py set-topic --name report
pause
goto MENU

:TOPIC_SYSTEM
cls
%SDE_PYTHON_CMD% -u tools\telegram_settings.py set-topic --name system
pause
goto MENU

:TOPIC_NEWS
cls
echo Memvalidasi dan menyimpan topic News: 1451
echo.
%SDE_PYTHON_CMD% -u tools\telegram_settings.py set-topic --name news --thread-id 1451
pause
goto MENU

:STATUS
cls
%SDE_PYTHON_CMD% -u tools\telegram_settings.py status
pause
goto MENU

:TEST_ALL
cls
%SDE_PYTHON_CMD% -u tools\telegram_settings.py test-all
pause
goto MENU

:SET_BRAVE
cls
%SDE_PYTHON_CMD% -u tools\telegram_settings.py set-brave
pause
goto MENU

:TEST_BRAVE
cls
%SDE_PYTHON_CMD% -u tools\telegram_settings.py test-brave
pause
goto MENU

:AUTO_REPORT
cls
echo Auto-detect REPORT TEST + Validate + Simpan
echo Kirim tepat REPORT TEST di topic Report sebelum melanjutkan.
echo.
set "REPORT_ID="
for /f "usebackq delims=" %%I in (`"%SDE_PYTHON_CMD% -u tools\detect_telegram_report_topic.py --marker "REPORT TEST" --id-only" 2^>nul`) do set "REPORT_ID=%%I"
if not defined REPORT_ID (
  echo [FAILED] REPORT TEST belum dapat dideteksi pada chat SDE.
  pause
  goto MENU
)
echo [DETECTED] message_thread_id=!REPORT_ID!
echo Memvalidasi topic dengan sendMessage Telegram silent + auto-delete...
%SDE_PYTHON_CMD% -u tools\check_telegram_report_route.py --thread-id "!REPORT_ID!" --definitive
if errorlevel 1 (
  echo [FAILED] REPORT topic TIDAK disimpan karena sendMessage Telegram gagal.
  pause
  goto MENU
)
setx TELEGRAM_THREAD_REPORT_ID "!REPORT_ID!" >nul
if errorlevel 1 (
  echo [FAILED] Gagal menyimpan TELEGRAM_THREAD_REPORT_ID.
  pause
  goto MENU
)
%SDE_PYTHON_CMD% -u tools\telegram_settings.py set-topic --name report --thread-id "!REPORT_ID!"
echo [OK] REPORT topic tervalidasi dan disimpan: !REPORT_ID!
pause
goto MENU

:SHOW
cls
echo Kirim satu pesan di topic yang ingin dicek terlebih dahulu.
echo.
%SDE_PYTHON_CMD% -u modules\telegram\get_chat_id.py
pause
goto MENU
