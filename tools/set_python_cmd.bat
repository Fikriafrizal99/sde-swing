@echo off
if defined SDE_PYTHON_CMD if defined SDE_PYTHON_CMD_VALIDATED goto :eof

if defined SDE_PYTHON_CMD (
  call :validate_python_cmd
  if not errorlevel 1 (
    set "SDE_PYTHON_CMD_VALIDATED=1"
    goto :eof
  )
)

set "SDE_PYTHON_CMD="
set "SDE_PYTHON_CMD_VALIDATED="

if defined SDE_PYTHON (
  set "SDE_PYTHON_CMD=%SDE_PYTHON%"
  call :validate_python_cmd
  if not errorlevel 1 goto :python_found
  set "SDE_PYTHON_CMD="
)

if exist "%~dp0..\.venv\Scripts\python.exe" (
  set "SDE_PYTHON_CMD="%~dp0..\.venv\Scripts\python.exe""
  goto :python_found
)

where python >nul 2>nul
if not errorlevel 1 (
  set "SDE_PYTHON_CMD=python"
  goto :python_found
)

where py >nul 2>nul
if not errorlevel 1 (
  set "SDE_PYTHON_CMD=py -3"
  goto :python_found
)

for %%V in (313 312 311 310) do (
  if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" (
    set "SDE_PYTHON_CMD="%LocalAppData%\Programs\Python\Python%%V\python.exe""
    goto :python_found
  )
)

goto :eof

:python_found
set "SDE_PYTHON_CMD_VALIDATED=1"
goto :eof

:validate_python_cmd
if /I "%SDE_PYTHON_CMD%"=="python" (
  where python >nul 2>nul
  exit /b %ERRORLEVEL%
)
if /I "%SDE_PYTHON_CMD%"=="py -3" (
  where py >nul 2>nul
  exit /b %ERRORLEVEL%
)
if exist %SDE_PYTHON_CMD% exit /b 0
exit /b 1
