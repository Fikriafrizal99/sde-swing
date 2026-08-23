@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title SDE Swing - Market Outlook

:MENU
cls
echo ================================================================
echo                 SDE SWING - MARKET OUTLOOK
echo ================================================================
echo.
echo [1] Normal - refresh dan kirim Telegram ^(hari trading^)
echo [2] Preview existing - sesi trading terakhir, read-only
echo [3] Kirim ulang - delivery-only hasil hari trading terakhir
echo [4] Cek status Market Outlook
echo [5] Normal + Morning News - satu kali jalan
echo [6] Morning News Only
echo [7] Preview Morning News existing
echo [8] Force Send Morning News existing
echo [9] Recovery sesi terakhir terlewat - historical/as-of, tanpa Telegram
echo [0] Kembali
echo.
echo Recovery Market Outlook memakai data point-in-time pada jam Outlook target,
echo IHSG/technical context sesi sebelumnya, dan tidak menjalankan Decision Engine.
echo.
set "MODE="
set /p "MODE=Pilih mode: "
if "%MODE%"=="0" exit /b 0
call tools\set_python_cmd.bat
if not defined SDE_PYTHON_CMD goto PYTHON_MISSING

if "%MODE%"=="4" goto STATUS_ONLY
if "%MODE%"=="3" goto RESEND
if "%MODE%"=="2" goto PREVIEW_EXISTING
if "%MODE%"=="5" goto NORMAL_WITH_NEWS
if "%MODE%"=="6" goto NEWS_ONLY
if "%MODE%"=="7" goto NEWS_PREVIEW
if "%MODE%"=="8" goto NEWS_FORCE_SEND
if "%MODE%"=="9" goto RECOVER_LAST_SESSION
if not "%MODE%"=="1" goto MENU

%SDE_PYTHON_CMD% -u run_sde_job_integrated.py --job market_outlook
set "RC=!ERRORLEVEL!"
goto STATUS

:PREVIEW_EXISTING
set "PREVIEW_DATE="
for /f "usebackq delims=" %%D in (`"%SDE_PYTHON_CMD% tools\resolve_last_trading_day.py" 2^>nul`) do set "PREVIEW_DATE=%%D"
if not defined PREVIEW_DATE goto PREVIEW_DATE_FAILED

echo.
echo Membuka preview Market Outlook trade date !PREVIEW_DATE! dari artifact existing...
%SDE_PYTHON_CMD% -u tools\resend_daily_report.py --job market_outlook --trade-date !PREVIEW_DATE! --preview-only
set "RC=!ERRORLEVEL!"
goto STATUS

:RECOVER_LAST_SESSION
echo.
echo ================================================================
echo MARKET OUTLOOK RECOVERY - HISTORICAL / AS-OF
echo ================================================================
echo Recovery otomatis memilih sesi IDX terakhir yang sudah selesai.
echo Data global dibatasi sampai sesi yang sudah diketahui pada jam Outlook target.
echo Telegram OFF. Decision/quant engine tidak dijalankan.
echo.
%SDE_PYTHON_CMD% -u tools\recover_market_outlook.py
set "RC=!ERRORLEVEL!"
goto STATUS

:NORMAL_WITH_NEWS
%SDE_PYTHON_CMD% -u run_sde_job_integrated.py --job market_outlook
set "RC=!ERRORLEVEL!"
if "!RC!"=="0" (
  echo.
  echo [NEWS] Menjalankan Morning News non-blocking...
  %SDE_PYTHON_CMD% -u modules\news\news_monitor_fresh_grouped.py run --session morning --send
  set "NEWS_RC=!ERRORLEVEL!"
  if not "!NEWS_RC!"=="0" echo [WARNING] Morning News gagal/dilewati ^(exit !NEWS_RC!^). Market Outlook tetap SUCCESS.
) else (
  echo [SKIPPED] Morning News tidak dijalankan karena Market Outlook exit code !RC!.
)
goto STATUS

:NEWS_ONLY
%SDE_PYTHON_CMD% -u modules\news\news_monitor_fresh_grouped.py run --session morning --send
set "NEWS_RC=!ERRORLEVEL!"
echo.
echo News exit code: !NEWS_RC!
pause
goto MENU

:NEWS_PREVIEW
%SDE_PYTHON_CMD% -u modules\news\news_monitor_fresh_grouped.py preview --session morning
pause
goto MENU

:NEWS_FORCE_SEND
%SDE_PYTHON_CMD% -u modules\news\news_monitor_fresh_grouped.py send --session morning --force
set "NEWS_RC=!ERRORLEVEL!"
echo.
echo News exit code: !NEWS_RC!
pause
goto MENU

:RESEND
set "RESEND_DATE="
for /f "usebackq delims=" %%D in (`"%SDE_PYTHON_CMD% tools\resolve_last_trading_day.py" 2^>nul`) do set "RESEND_DATE=%%D"
if not defined RESEND_DATE goto RESEND_DATE_FAILED

echo.
echo Mengirim ulang Market Outlook trade date %RESEND_DATE% tanpa menjalankan engine...
%SDE_PYTHON_CMD% -u tools\resend_daily_report.py --job market_outlook --trade-date %RESEND_DATE%
set "RC=!ERRORLEVEL!"
goto STATUS

:STATUS_ONLY
%SDE_PYTHON_CMD% tools\print_job_status.py --job market_outlook
pause
goto MENU

:PREVIEW_DATE_FAILED
echo Gagal menentukan hari trading terakhir untuk Preview Existing.
pause
goto MENU

:RESEND_DATE_FAILED
echo Gagal menentukan hari trading terakhir.
pause
goto MENU

:PYTHON_MISSING
echo Python tidak ditemukan. Jalankan maintenance\INSTALL_REQUIREMENTS.bat.
pause
goto MENU

:STATUS
echo.
%SDE_PYTHON_CMD% tools\print_job_status.py --job market_outlook
echo.
echo Exit code: !RC!
pause
goto MENU
