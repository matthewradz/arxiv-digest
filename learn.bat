@echo off
REM Retune the digest from the papers you marked as wanted.



setlocal EnableDelayedExpansion
cd /d "%~dp0"
call 
:findpy
set "PY="
if exist "%~dp0.python-path" (
  set /p PY=<"%~dp0.python-path"
  if defined PY (
    "%PY%" -c "import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)" 2>nul
    if errorlevel 1 set "PY="
  )
)
if not defined PY if defined CONDA_PREFIX (
  if exist "%CONDA_PREFIX%\python.exe" set "PY=%CONDA_PREFIX%\python.exe"
)
if not defined PY if defined VIRTUAL_ENV (
  if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "PY=%VIRTUAL_ENV%\Scripts\python.exe"
)
if not defined PY (
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
)
exit /b 0
