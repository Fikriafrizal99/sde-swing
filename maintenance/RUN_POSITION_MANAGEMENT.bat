@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0\.."
title SDE - Active Portfolio Management

set "NON_BLOCKING=0"
if /I "%~1"=="--non-blocking" set "NON_BLOCKING=1"
set "FORCE_ARG=--force"
if "%NON_BLOCKING%"=="1" set "FORCE_ARG="

call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo [FAILED] Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  if "%NON_BLOCKING%"=="0" pause
  if "%NON_BLOCKING%"=="1" exit /b 0
  exit /b 9009
)

set "TRADE_DATE="
for /f "delims=" %%D in ('%SDE_PYTHON_CMD% tools\resolve_last_trading_day.py 2^>nul') do set "TRADE_DATE=%%D"
if not defined TRADE_DATE (
  echo [FAILED] Tidak dapat menentukan hari trading IDX terakhir.
  if "%NON_BLOCKING%"=="0" pause
  if "%NON_BLOCKING%"=="1" exit /b 0
  exit /b 1
)

%SDE_PYTHON_CMD% -u tools\check_telegram_report_route.py
set "ROUTE_RC=!ERRORLEVEL!"
if not "!ROUTE_RC!"=="0" (
  echo [WARNING] Portfolio Management tetap dianalisis, tetapi Telegram dilewati agar tidak masuk topic yang salah.
)

echo.
echo ================================================================
echo          SDE - ACTIVE PORTFOLIO MANAGEMENT
echo ================================================================
echo Trade date : !TRADE_DATE!
echo Engine utama tidak dijalankan ulang.
echo Hanya posisi portfolio aktual dengan status OPEN yang dianalisis.
echo Broker context: Current + 3D + 5D + 7D + Since Entry.
if "!ROUTE_RC!"=="0" (
  if "%NON_BLOCKING%"=="0" echo Delivery    : MANUAL FORCE RESEND ke topic Report.
  if "%NON_BLOCKING%"=="1" echo Delivery    : AUTO DEDUPE ke topic Report.
) else (
  echo Delivery    : SKIPPED - topic Report belum valid/terpisah.
)
echo.

echo [1/2] Refresh data posisi OPEN...
%SDE_PYTHON_CMD% -u modules\portfolio\refresh_open_positions.py --config config\pipeline.json --trade-date "!TRADE_DATE!"
set "REFRESH_RC=!ERRORLEVEL!"
if not "!REFRESH_RC!"=="0" (
  echo [WARNING] Refresh posisi OPEN tidak lengkap. Analisis dilanjutkan dengan last valid local data.
)

echo.
echo [2/2] Jalankan Position Management...
if "!ROUTE_RC!"=="0" (
  %SDE_PYTHON_CMD% -u modules\portfolio\position_management_runtime.py --config config\pipeline.json --scheduler-config config\scheduler.json --trade-date "!TRADE_DATE!" --telegram !FORCE_ARG!
) else (
  %SDE_PYTHON_CMD% -u modules\portfolio\position_management_runtime.py --config config\pipeline.json --scheduler-config config\scheduler.json --trade-date "!TRADE_DATE!" --no-telegram
)
set "RC=!ERRORLEVEL!"
if "!ROUTE_RC!"=="0" %SDE_PYTHON_CMD% -u modules\portfolio\portfolio_delivery_status.py

echo.
if "!RC!"=="0" (
  echo [OK] Position Management selesai.
) else if "!RC!"=="50" (
  echo [WARNING] Analisis selesai tetapi delivery Telegram gagal.
) else (
  echo [FAILED] Position Management gagal. Exit code !RC!.
)

if "%NON_BLOCKING%"=="1" (
  if not "!RC!"=="0" echo [NON-BLOCKING] Kegagalan Position Management tidak mengubah status engine utama.
  exit /b 0
)

pause
exit /b !RC!
