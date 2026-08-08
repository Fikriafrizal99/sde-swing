@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0\.."
title SDE - Actual Portfolio Maintenance

call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  pause
  exit /b 9009
)

:MENU
cls
echo ================================================================
echo          SDE - PORTFOLIO AKTUAL PENGGUNA
echo ================================================================
echo Source of truth: data\database\sde_swing_history.db
echo.
echo [1] Catat BUY aktual
echo [2] Catat SELL aktual
echo [3] Lihat portfolio
echo [4] Isi TP/SL manual posisi OPEN
echo [0] Keluar
echo.
set "ACTION="
set /p "ACTION=Pilih menu: "
if "!ACTION!"=="1" goto BUY
if "!ACTION!"=="2" goto SELL
if "!ACTION!"=="3" goto LIST
if "!ACTION!"=="4" goto MANUAL_PLAN
if "!ACTION!"=="0" exit /b 0
echo Pilihan tidak valid.
pause
goto MENU

:BUY
cls
echo --- CATAT BUY AKTUAL ---
set "SYMBOL="
set "QTY="
set "PRICE="
set "BUY_DATE="
set "SIGNAL_ID="
set "NOTES="
set "INITIAL_SL="
set "TP1="
set "TP2="
set /p "SYMBOL=Symbol (contoh BBCA): "
set /p "QTY=Quantity saham: "
set /p "PRICE=Harga beli aktual: "
set /p "BUY_DATE=Tanggal beli YYYY-MM-DD (kosong = hari ini): "
set /p "SIGNAL_ID=signal_id (kosong = cari rekomendasi aktif symbol): "
set /p "NOTES=Catatan (opsional): "
echo.
echo Jika BUY berasal dari luar rekomendasi mesin, TP/SL boleh diisi sekarang.
echo Jika kosong, Position Management tetap jalan tetapi initial plan akan ditandai belum lengkap.
set /p "INITIAL_SL=Initial SL (opsional): "
set /p "TP1=TP1 (opsional): "
set /p "TP2=TP2 (opsional): "
set "DATE_ARG="
set "SIGNAL_ARG="
set "NOTES_ARG="
if defined BUY_DATE set "DATE_ARG=--buy-date !BUY_DATE!"
if defined SIGNAL_ID set "SIGNAL_ARG=--signal-id !SIGNAL_ID!"
if defined NOTES set NOTES_ARG=--notes "!NOTES!"
%SDE_PYTHON_CMD% -u modules\analytics\outcome_tracker.py portfolio --db data\database\sde_swing_history.db record-buy --symbol "!SYMBOL!" --quantity "!QTY!" --price "!PRICE!" !DATE_ARG! !SIGNAL_ARG! !NOTES_ARG!
set "RC=!ERRORLEVEL!"
echo.
if not "!RC!"=="0" (
  echo [FAILED] BUY gagal. Exit code !RC!.
  pause
  goto MENU
)
echo [OK] BUY aktual tersimpan.

set "PLAN_ARGS="
if defined INITIAL_SL set "PLAN_ARGS=!PLAN_ARGS! --sl !INITIAL_SL!"
if defined TP1 set "PLAN_ARGS=!PLAN_ARGS! --tp1 !TP1!"
if defined TP2 set "PLAN_ARGS=!PLAN_ARGS! --tp2 !TP2!"
if defined PLAN_ARGS (
  set "PLAN_DATE_ARG="
  if defined BUY_DATE set "PLAN_DATE_ARG=--buy-date !BUY_DATE!"
  %SDE_PYTHON_CMD% -u modules\portfolio\manual_position_plan.py --db data\database\sde_swing_history.db set --symbol "!SYMBOL!" --quantity "!QTY!" --buy-price "!PRICE!" !PLAN_DATE_ARG! !PLAN_ARGS! --setup MANUAL
  set "PLAN_RC=!ERRORLEVEL!"
  if "!PLAN_RC!"=="0" (
    echo [OK] Initial TP/SL manual tersimpan tanpa menimpa plan mesin.
  ) else (
    echo [WARNING] BUY sudah tersimpan, tetapi initial TP/SL manual gagal. Exit code !PLAN_RC!.
  )
)
pause
goto MENU

:SELL
cls
echo --- CATAT SELL AKTUAL ---
set "POSITION_ID="
set "SYMBOL="
set "PRICE="
set "SELL_DATE="
set /p "POSITION_ID=position_id (kosong jika pakai symbol): "
set /p "SYMBOL=Symbol jika position_id kosong: "
set /p "PRICE=Harga jual aktual: "
set /p "SELL_DATE=Tanggal jual YYYY-MM-DD (kosong = hari ini): "
set "POSITION_ARG="
set "SYMBOL_ARG="
set "DATE_ARG="
if defined POSITION_ID set "POSITION_ARG=--position-id !POSITION_ID!"
if defined SYMBOL set "SYMBOL_ARG=--symbol !SYMBOL!"
if defined SELL_DATE set "DATE_ARG=--sell-date !SELL_DATE!"
%SDE_PYTHON_CMD% -u modules\analytics\outcome_tracker.py portfolio --db data\database\sde_swing_history.db record-sell --price "!PRICE!" !POSITION_ARG! !SYMBOL_ARG! !DATE_ARG!
set "RC=!ERRORLEVEL!"
echo.
if "!RC!"=="0" (echo [OK] SELL aktual tersimpan.) else (echo [FAILED] SELL gagal. Exit code !RC!.)
pause
goto MENU

:LIST
cls
%SDE_PYTHON_CMD% -u modules\analytics\outcome_tracker.py portfolio --db data\database\sde_swing_history.db list
pause
goto MENU

:MANUAL_PLAN
cls
echo --- ISI TP/SL MANUAL POSISI OPEN ---
set "POSITION_ID="
set "SYMBOL="
set "INITIAL_SL="
set "TP1="
set "TP2="
set /p "POSITION_ID=position_id (opsional): "
set /p "SYMBOL=Symbol jika position_id kosong: "
set /p "INITIAL_SL=Initial SL (opsional): "
set /p "TP1=TP1 (opsional): "
set /p "TP2=TP2 (opsional): "
set "POSITION_ARG="
set "SYMBOL_ARG="
set "PLAN_ARGS="
if defined POSITION_ID set "POSITION_ARG=--position-id !POSITION_ID!"
if defined SYMBOL set "SYMBOL_ARG=--symbol !SYMBOL!"
if defined INITIAL_SL set "PLAN_ARGS=!PLAN_ARGS! --sl !INITIAL_SL!"
if defined TP1 set "PLAN_ARGS=!PLAN_ARGS! --tp1 !TP1!"
if defined TP2 set "PLAN_ARGS=!PLAN_ARGS! --tp2 !TP2!"
if not defined PLAN_ARGS (
  echo [SKIPPED] Tidak ada TP/SL yang diisi.
  pause
  goto MENU
)
%SDE_PYTHON_CMD% -u modules\portfolio\manual_position_plan.py --db data\database\sde_swing_history.db set !POSITION_ARG! !SYMBOL_ARG! !PLAN_ARGS! --setup MANUAL
set "RC=!ERRORLEVEL!"
if "!RC!"=="0" (
  echo [OK] Manual initial plan tersimpan. Field plan mesin yang sudah ada tidak ditimpa.
) else (
  echo [FAILED] Manual initial plan gagal. Exit code !RC!.
)
pause
goto MENU