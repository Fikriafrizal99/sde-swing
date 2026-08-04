@echo off
set "SDE_PYTHON_CMD="

if defined SDE_PYTHON (
  set "SDE_PYTHON_CMD=%SDE_PYTHON%"
  goto :eof
)

where python >nul 2>nul
if not errorlevel 1 (
  set "SDE_PYTHON_CMD=python"
  goto :eof
)

where py >nul 2>nul
if not errorlevel 1 (
  set "SDE_PYTHON_CMD=py -3"
  goto :eof
)

for %%V in (313 312 311 310) do (
  if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" (
    set "SDE_PYTHON_CMD="%LocalAppData%\Programs\Python\Python%%V\python.exe""
    goto :eof
  )
)

goto :eof
