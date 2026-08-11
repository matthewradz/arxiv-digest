@echo off
REM Nightly arXiv digest.
REM   run.bat              build tonight's digest
REM   run.bat --force      rebuild even if it already exists
REM   run.bat --open       build, then open it
setlocal
cd /d "%~dp0"
call :findpy
if "%PY%"=="" (
  echo Python 3.11 or newer is required but was not found.
  echo Install it from https://www.python.org/downloads/ and tick
  echo "Add python.exe to PATH" during setup.
  exit /b 127
)
"%PY%" pipeline.py digest %*
exit /b %ERRORLEVEL%

:findpy
set "PY="
for %%C in (py.exe python.exe python3.exe) do (
  if not defined PY (
    for /f "delims=" %%P in ('where %%C 2^>nul') do (
      if not defined PY (
        "%%P" -c "import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)" 2>nul
        if not errorlevel 1 set "PY=%%P"
      )
    )
  )
)
exit /b 0
