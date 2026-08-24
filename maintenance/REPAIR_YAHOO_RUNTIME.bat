@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0\.."
title SDE - Repair Yahoo/YFinance Runtime

call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo [FAILED] Python tidak ditemukan.
  pause
  exit /b 9009
)

echo ================================================================
echo         FTJ Community - REPAIR YAHOO / YFINANCE RUNTIME
echo ================================================================
echo.
echo Python yang dipakai SDE:
%SDE_PYTHON_CMD% -c "import sys; print(sys.executable); print(sys.version)"
echo.
echo Repair ini hanya memperbaiki package runtime Yahoo/protobuf.
echo Tidak mengubah Candidate Selector, Broker Fusion, Decision, Exit,
echo Final Watchlist, config scoring, atau data portfolio.
echo.

set "CONFIRM_REPAIR="
set /p "CONFIRM_REPAIR=Lanjut repair dependency? [Y/N]: "
if /I not "%CONFIRM_REPAIR%"=="Y" exit /b 0

echo.
echo [1/3] Force reinstall protobuf...
%SDE_PYTHON_CMD% -m pip install --disable-pip-version-check --upgrade --force-reinstall "protobuf>=5,<8"
if errorlevel 1 (
  echo [FAILED] Reinstall protobuf gagal.
  pause
  exit /b 1
)

echo.
echo [2/3] Force reinstall yfinance tanpa mengubah dependency lain...
%SDE_PYTHON_CMD% -m pip install --disable-pip-version-check --upgrade --force-reinstall --no-deps "yfinance>=0.2.40"
if errorlevel 1 (
  echo [FAILED] Reinstall yfinance gagal.
  pause
  exit /b 1
)

echo.
echo [3/3] Validasi import runtime...
%SDE_PYTHON_CMD% -c "import yfinance as yf, google.protobuf as pb; print('[OK] Yahoo runtime siap | yfinance=' + str(yf.__version__) + ' | protobuf=' + str(pb.__version__))"
if errorlevel 1 (
  echo.
  echo [FAILED] Import masih bermasalah.
  echo Jalankan Maintenance ^> Install requirements, lalu ulangi repair ini.
  pause
  exit /b 2
)

echo.
echo [OK] Repair Yahoo/YFinance selesai.
pause
exit /b 0
