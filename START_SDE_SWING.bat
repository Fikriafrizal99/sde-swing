@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title SDE Swing Control Panel

:MENU
cls
echo ==========================================
echo        SDE SWING CONTROL PANEL
echo ==========================================
echo.
echo  1. Jalankan Alur Harian Lengkap
echo  2. Market Outlook
echo  3. Post Market
echo  4. Broker Summary
echo  5. Broker Multi-Day
echo  6. Final Watchlist
echo  7. Cek Status Sistem
echo  8. Preview Final Watchlist Tanpa Telegram
echo  9. Jalankan Test Validasi
echo 10. Buka Folder Output
echo 11. Buka File .env
echo  0. Keluar
echo.
set /p CHOICE=Pilih menu: 

if "%CHOICE%"=="1" goto FULL_DAILY
if "%CHOICE%"=="2" goto MARKET_OUTLOOK
if "%CHOICE%"=="3" goto POST_MARKET
if "%CHOICE%"=="4" goto BROKER_SUMMARY
if "%CHOICE%"=="5" goto BROKER_MULTI_DAY
if "%CHOICE%"=="6" goto FINAL_WATCHLIST
if "%CHOICE%"=="7" goto STATUS
if "%CHOICE%"=="8" goto PREVIEW
if "%CHOICE%"=="9" goto TESTS
if "%CHOICE%"=="10" goto OUTPUT
if "%CHOICE%"=="11" goto ENV_FILE
if "%CHOICE%"=="0" goto END

echo.
echo Pilihan tidak valid.
pause
goto MENU

:CHECK_PYTHON
where python >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] Python tidak ditemukan di PATH.
  echo Pasang Python atau aktifkan virtual environment terlebih dahulu.
  pause
  goto MENU
)
exit /b 0

:RUN_JOB
call :CHECK_PYTHON
if errorlevel 1 goto MENU
set JOB_NAME=%~1
set EXTRA_ARGS=%~2
echo.
echo Menjalankan: %JOB_NAME%
echo ------------------------------------------
python -u run_sde_job_integrated.py --job %JOB_NAME% %EXTRA_ARGS%
set EXIT_CODE=%ERRORLEVEL%
echo.
if "%EXIT_CODE%"=="0" (
  echo [SUCCESS] %JOB_NAME% selesai.
) else (
  echo [FAILED] %JOB_NAME% berhenti dengan exit code %EXIT_CODE%.
)
echo.
pause
goto MENU

:FULL_DAILY
call :RUN_JOB full_manual "--interactive-broker"
goto MENU

:MARKET_OUTLOOK
call :RUN_JOB market_outlook ""
goto MENU

:POST_MARKET
call :RUN_JOB post_market ""
goto MENU

:BROKER_SUMMARY
call :RUN_JOB broker_summary ""
goto MENU

:BROKER_MULTI_DAY
call :RUN_JOB broker_multi_day ""
goto MENU

:FINAL_WATCHLIST
call :RUN_JOB final_watchlist "--interactive-broker"
goto MENU

:STATUS
call :CHECK_PYTHON
if errorlevel 1 goto MENU
python -u run_sde_job.py --job job_status --no-telegram
echo.
if exist "data\output\job_status" (
  echo Folder status: %CD%\data\output\job_status
  start "" "data\output\job_status"
)
pause
goto MENU

:PREVIEW
call :CHECK_PYTHON
if errorlevel 1 goto MENU
python -u run_sde_job_integrated.py --job final_watchlist --preview-existing --no-telegram
set EXIT_CODE=%ERRORLEVEL%
echo.
if "%EXIT_CODE%"=="0" (
  echo [SUCCESS] Preview dibuat tanpa mengirim Telegram.
  if exist "data\output\previews" start "" "data\output\previews"
) else (
  echo [FAILED] Preview gagal dengan exit code %EXIT_CODE%.
)
pause
goto MENU

:TESTS
call :CHECK_PYTHON
if errorlevel 1 goto MENU
echo.
echo Menjalankan test validasi...
python -m pytest -q
set EXIT_CODE=%ERRORLEVEL%
echo.
if "%EXIT_CODE%"=="0" (
  echo [SUCCESS] Seluruh test lulus.
) else (
  echo [FAILED] Ada test gagal. Exit code %EXIT_CODE%.
)
pause
goto MENU

:OUTPUT
if not exist "data\output" mkdir "data\output"
start "" "data\output"
goto MENU

:ENV_FILE
if not exist ".env" (
  if exist ".env.example" copy /Y ".env.example" ".env" >nul
)
if exist ".env" (
  notepad ".env"
) else (
  echo File .env dan .env.example tidak ditemukan.
  pause
)
goto MENU

:END
endlocal
exit /b 0
