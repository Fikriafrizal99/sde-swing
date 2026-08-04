@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title SDE Swing V1.7.0 Multi-Source

set "SDE_ROOT=%CD%"
set "SDE_LOG=%SDE_ROOT%\logs\start_sde_swing.log"
if not exist "%SDE_ROOT%\logs" mkdir "%SDE_ROOT%\logs"

call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD (
  echo.
  echo [ERROR] Python tidak ditemukan.
  echo Aktifkan environment Python atau jalankan maintenance\INSTALL_REQUIREMENTS.bat.
  call :LOG "startup_status=PYTHON_NOT_FOUND exit_code=9009"
  pause
  exit /b 9009
)
set "PYTHON_CMD=%SDE_PYTHON_CMD%"
set "PYTHON_VERSION=UNKNOWN"
for /f "delims=" %%V in ('%PYTHON_CMD% --version 2^>^&1') do set "PYTHON_VERSION=%%V"
set "GIT_BRANCH=UNKNOWN"
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "GIT_BRANCH=%%B"
call :LOG "startup_status=READY branch=!GIT_BRANCH! python=!PYTHON_VERSION! root=!SDE_ROOT!"

:MENU
cls
echo ================================================================
echo              SDE SWING V1.7.0 MULTI-SOURCE
echo ================================================================
echo Root   : %SDE_ROOT%
echo Branch : %GIT_BRANCH%
echo Python : %PYTHON_VERSION%
echo.
echo  1. Jalankan Alur Harian Lengkap
echo  2. Market Outlook
echo  3. Post Market
echo  4. Broker Summary
echo  5. Broker Multi-Day
echo  6. Final Watchlist - Run Normal
echo  7. Cek Status Sistem
echo  8. Final Watchlist - Preview Existing
echo  9. Jalankan Test Validasi
echo 10. Buka Folder Output
echo 11. Test Telegram Terpisah
echo 12. Register Semua BUY Mesin
echo 13. Maintain Portfolio Aktual
echo  0. Keluar
echo.
set "CHOICE="
set /p "CHOICE=Pilih menu: "

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
if "%CHOICE%"=="11" goto TELEGRAM_TEST
if "%CHOICE%"=="12" goto REGISTER_BUY
if "%CHOICE%"=="13" goto PORTFOLIO
if "%CHOICE%"=="0" goto END

echo.
echo Pilihan tidak valid. Kembali ke menu.
call :LOG "selected_menu=!CHOICE! status=INVALID_SELECTION"
pause
goto MENU

:FULL_DAILY
call :RUN_JOB "full_manual" "--interactive-broker" "1 Full Manual"
goto MENU

:MARKET_OUTLOOK
call :RUN_JOB "market_outlook" "" "2 Market Outlook"
goto MENU

:POST_MARKET
call :RUN_JOB "post_market" "" "3 Post Market"
goto MENU

:BROKER_SUMMARY
call :RUN_JOB "broker_summary" "" "4 Broker Summary"
goto MENU

:BROKER_MULTI_DAY
call :RUN_JOB "broker_multi_day" "" "5 Broker Multi-Day"
goto MENU

:FINAL_WATCHLIST
call :RUN_JOB "final_watchlist" "--interactive-broker" "6 Final Watchlist Normal"
goto MENU

:STATUS
call :RUN_JOB "job_status" "--no-telegram" "7 Job Status"
for %%J in (market_outlook post_market broker_summary broker_multi_day final_watchlist full_manual) do (
  %PYTHON_CMD% tools\print_job_status.py --job %%J
  echo.
)
pause
goto MENU

:PREVIEW
call :RUN_JOB "final_watchlist" "--preview-existing --no-telegram" "8 Final Watchlist Preview Existing"
if exist "data\output\previews" start "" "data\output\previews"
goto MENU

:TESTS
echo.
echo Menjalankan test validasi...
call :LOG "selected_menu=9 command=%PYTHON_CMD% -m pytest -q branch=!GIT_BRANCH! python=!PYTHON_VERSION!"
%PYTHON_CMD% -m pytest -q
set "EXIT_CODE=!ERRORLEVEL!"
if "!EXIT_CODE!"=="0" (
  echo [OK] Seluruh test lulus.
) else (
  echo [FAILED] Test gagal. Exit code !EXIT_CODE!.
)
call :LOG "selected_menu=9 exit_code=!EXIT_CODE!"
echo Log: %SDE_LOG%
pause
goto MENU

:OUTPUT
if not exist "data\output" mkdir "data\output"
call :LOG "selected_menu=10 action=OPEN_OUTPUT path=%SDE_ROOT%\data\output"
start "" "data\output"
goto MENU

:TELEGRAM_TEST
echo.
echo Menjalankan test delivery Telegram terpisah...
call :LOG "selected_menu=11 command=%PYTHON_CMD% modules\telegram\telegram_bot.py --config config\telegram.json test"
%PYTHON_CMD% -u modules\telegram\telegram_bot.py --config config\telegram.json test
set "EXIT_CODE=!ERRORLEVEL!"
if "!EXIT_CODE!"=="0" (
  echo [OK] Test Telegram selesai.
) else if "!EXIT_CODE!"=="10" (
  echo [SKIPPED_NOT_CONFIGURED] Token atau chat ID Telegram belum tersedia. Engine tidak dijalankan ulang.
) else (
  echo [DELIVERY_FAILED] Test Telegram gagal. Engine tidak dijalankan ulang. Exit code !EXIT_CODE!.
)
call :LOG "selected_menu=11 delivery_status=!EXIT_CODE! exit_code=!EXIT_CODE!"
echo Log: %SDE_LOG%
pause
goto MENU

:REGISTER_BUY
call :LOG "selected_menu=12 action=REGISTER_BUY_SIGNALS"
call maintenance\RECORD_BUY_SIGNALS.bat
goto MENU

:PORTFOLIO
call :LOG "selected_menu=13 action=MAINTAIN_PORTFOLIO"
call maintenance\RECORD_PORTFOLIO_BUY.bat
goto MENU

:RUN_JOB
set "JOB_NAME=%~1"
set "EXTRA_ARGS=%~2"
set "SELECTED_MENU=%~3"
set "JOB_COMMAND=%PYTHON_CMD% -u run_sde_job_integrated.py --job !JOB_NAME! !EXTRA_ARGS!"
echo.
echo Menjalankan: !JOB_NAME!
echo Command    : !JOB_COMMAND!
echo ----------------------------------------------------------------
call :LOG "selected_menu=!SELECTED_MENU! command=!JOB_COMMAND! branch=!GIT_BRANCH! python=!PYTHON_VERSION!"
%PYTHON_CMD% -u run_sde_job_integrated.py --job !JOB_NAME! !EXTRA_ARGS!
set "EXIT_CODE=!ERRORLEVEL!"
call :SHOW_RESULT "!EXIT_CODE!" "!JOB_NAME!"
set "STATUS_FIELDS=status_unavailable"
for /f "delims=" %%S in ('%PYTHON_CMD% tools\print_job_status.py --job !JOB_NAME! --log-fields 2^>nul') do set "STATUS_FIELDS=%%S"
set "LATEST_MANIFEST=%SDE_ROOT%\data\output\job_status\!JOB_NAME!_latest.json"
call :LOG "selected_menu=!SELECTED_MENU! exit_code=!EXIT_CODE! !STATUS_FIELDS! latest_manifest=!LATEST_MANIFEST!"
%PYTHON_CMD% tools\print_job_status.py --job !JOB_NAME!
echo.
echo Log: %SDE_LOG%
pause
exit /b 0

:SHOW_RESULT
set "RESULT_CODE=%~1"
set "RESULT_JOB=%~2"
if "%RESULT_CODE%"=="0" (
  set "LATEST_STATUS=SUCCESS"
  for /f "delims=" %%S in ('%PYTHON_CMD% tools\print_job_status.py --job %RESULT_JOB% --status-only 2^>nul') do set "LATEST_STATUS=%%S"
  if /I "!LATEST_STATUS!"=="SUCCESS_WITH_WARNING" (
    echo [OK WITH WARNING] %RESULT_JOB% selesai dengan peringatan.
  ) else (
    echo [OK] SUCCESS - %RESULT_JOB% selesai.
  )
  exit /b 0
)
if "%RESULT_CODE%"=="10" (
  echo [SKIPPED] Dependency atau kondisi job belum terpenuhi.
  exit /b 0
)
if "%RESULT_CODE%"=="20" (
  echo [WAITING_DATA] Data belum lengkap.
  exit /b 0
)
if "%RESULT_CODE%"=="30" (
  echo [DUPLICATE] Run sudah pernah diproses.
  exit /b 0
)
if "%RESULT_CODE%"=="40" (
  echo [LOCKED] Resource sedang dipakai.
  exit /b 0
)
if "%RESULT_CODE%"=="50" (
  echo [DELIVERY_FAILED] Engine/report mungkin berhasil, delivery gagal.
  exit /b 0
)
echo [FAILED] Runtime error. Exit code %RESULT_CODE%.
exit /b 0

:LOG
>> "%SDE_LOG%" echo [%date% %time%] %~1
exit /b 0

:END
call :LOG "selected_menu=0 status=EXIT"
endlocal
exit /b 0
