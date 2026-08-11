@echo off
REM ---------------------------------------------------------------------------
REM  Double-click this file to install the arXiv digest on Windows.
REM
REM  It copies everything to %USERPROFILE%\arxiv-digest, puts an "arXiv Digest"
REM  shortcut on your Desktop, and offers to run it automatically each weeknight.
REM  Nothing is installed system-wide and no administrator rights are needed.
REM ---------------------------------------------------------------------------
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title arXiv nightly digest - install

echo.
echo   arXiv nightly digest
echo   Reads the arXiv sections you choose each night, and writes you five papers.
echo.
echo   Checking what's on this PC
echo.

REM --- Python 3.11+ ----------------------------------------------------------
set "PY="
for %%C in (py.exe python.exe python3.exe) do (
  if not defined PY (
    for /f "delims=" %%P in ('where %%C 2^>nul') do (
      if not defined PY (
        "%%P" -c "import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
        if not errorlevel 1 set "PY=%%P"
      )
    )
  )
)

if not defined PY (
  echo   [X] No Python 3.11 or newer found.
  echo.
  echo       Install it from https://www.python.org/downloads/
  echo       IMPORTANT: tick "Add python.exe to PATH" during setup,
  echo       then double-click this installer again.
  echo.
  pause
  exit /b 1
)
for /f "delims=" %%V in ('"%PY%" -c "import sys;print(\"%%d.%%d\"%%sys.version_info[:2])"') do set "PYVER=%%V"
echo   [ok] Python !PYVER!  -  %PY%

REM --- a model CLI -----------------------------------------------------------
set "ENGINE="
for %%C in (claude.cmd claude.exe claude) do (
  if not defined ENGINE ( where %%C >nul 2>&1 && set "ENGINE=Claude Code" )
)
if not defined ENGINE (
  for %%C in (codex.cmd codex.exe codex) do (
    if not defined ENGINE ( where %%C >nul 2>&1 && set "ENGINE=Codex CLI" )
  )
)

if not defined ENGINE (
  echo   [X] No model CLI is installed.
  echo.
  echo       The digest needs one of these to read the abstracts. Install
  echo       whichever matches a subscription you already pay for.
  echo.
  echo       If you have ChatGPT Plus or Pro:
  echo           npm install -g @openai/codex
  echo           then run:  codex     and choose "Sign in with ChatGPT"
  echo.
  echo       If you have Claude Pro or Max:
  echo           irm https://claude.ai/install.ps1 ^| iex
  echo           then run:  claude    and sign in
  echo.
  echo       Sign in with the subscription, NOT an API key - an API key bills
  echo       separately per use, while the subscription is already paid for.
  echo.
  echo       Then double-click this installer again.
  echo.
  pause
  exit /b 1
)
echo   [ok] !ENGINE!
echo.

REM --- copy the program ------------------------------------------------------
set "DEST=%USERPROFILE%\arxiv-digest"
echo   Installing to %DEST%
if not exist "%DEST%" mkdir "%DEST%"
for %%D in (runs digests logs) do if not exist "%DEST%\%%D" mkdir "%DEST%\%%D"

for %%F in (fetch.py record.py pick.py learn.py app.py profile.py engine.py
            pipeline.py schedule.py run.bat learn.bat
            config.default.toml prompt.md learn_prompt.md README.md) do (
  if exist "%%F" copy /Y "%%F" "%DEST%\%%F" >nul
)

if exist "%DEST%\config.toml" (
  echo   [ok] your existing config.toml was left untouched
) else (
  copy /Y "config.default.toml" "%DEST%\config.toml" >nul
  echo   [ok] config.toml created from the defaults
)
echo   [ok] program files installed
echo.

REM --- launcher + Desktop shortcut ------------------------------------------
> "%DEST%\arXiv Digest.bat" echo @echo off
>> "%DEST%\arXiv Digest.bat" echo cd /d "%%~dp0"
>> "%DEST%\arXiv Digest.bat" echo start "" "%PY%" app.py

REM pythonw.exe runs without a console window. Fall back to python.exe if
REM this is a py.exe launcher install that has no pythonw beside it.
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" for /f "delims=" %%W in ('where pythonw.exe 2^>nul') do set "PYW=%%W"
if not exist "%PYW%" set "PYW=%PY%"

REM Build the shortcut from a generated .ps1 rather than an inline -Command:
REM escaping quotes through cmd into PowerShell is the single most fragile
REM thing in this script, and a temp file sidesteps it entirely.
set "PS1=%TEMP%\arxiv_digest_shortcut.ps1"
> "%PS1%" echo $w = New-Object -ComObject WScript.Shell
>> "%PS1%" echo $lnk = $w.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\arXiv Digest.lnk')
>> "%PS1%" echo $lnk.TargetPath = '%PYW%'
>> "%PS1%" echo $lnk.Arguments = '"%DEST%\app.py"'
>> "%PS1%" echo $lnk.WorkingDirectory = '%DEST%'
>> "%PS1%" echo $lnk.Description = 'arXiv nightly digest'
>> "%PS1%" echo $lnk.Save()
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" >nul 2>&1
del "%PS1%" >nul 2>&1

if exist "%USERPROFILE%\Desktop\arXiv Digest.lnk" (
  echo   [ok] "arXiv Digest" shortcut is on your Desktop
) else (
  echo   [!] could not make a Desktop shortcut - no problem, start it with:
  echo       "%DEST%\arXiv Digest.bat"
)
echo.

REM --- scheduling ------------------------------------------------------------
echo   How should it run?
echo.
echo     Either way, opening the app always builds the digest if it is not
echo     already there. The only question is whether it also runs on its own.
echo.
echo       automatic  - built quietly at 9pm Mon-Fri, so it is waiting for you.
echo       on demand  - nothing runs in the background. Opening the app builds
echo                    that evening's digest, which takes 2-3 minutes.
echo.
set "ANS=Y"
set /p "ANS=  Set up the automatic 9pm run? [Y/n] "
if /i "!ANS!"=="n" (
  echo   On demand only. Turn it on later with:
  echo     "%PY%" "%DEST%\schedule.py" install
) else (
  "%PY%" "%DEST%\schedule.py" install 21 0
  if errorlevel 1 (
    echo   [!] could not schedule it; the app still works
  ) else (
    echo   [ok] scheduled for 21:00, Monday to Friday
    echo       change the time:  "%PY%" "%DEST%\schedule.py" install 20 30
    echo       turn it off:      "%PY%" "%DEST%\schedule.py" remove
  )
)
echo.

echo   Done.
echo.
echo   Open "arXiv Digest" from your Desktop - it walks you through setup:
echo   signing in, whose work to follow, which arXiv sections, and where to
echo   save papers.
echo.
echo   The first digest takes a couple of minutes to build. After that it is
echo   instant.
echo.
echo   The one thing worth doing: click "Want this" on the papers you actually
echo   want (click again to undo). After a couple of weeks press "Retune from
echo   my picks" and it starts matching your taste. Edit
echo   %DEST%\config.toml to change the author list and topics.
echo.
pause
start "" "%PYW%" "%DEST%\app.py"
endlocal
