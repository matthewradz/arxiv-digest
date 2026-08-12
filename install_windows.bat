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

REM --- are the program files actually here? -----------------------------------
REM Double-clicking this file while it is still inside the zip extracts only the
REM .bat, to a temp folder of its own. Every copy below then finds nothing and
REM does nothing, and the install still reports success - leaving a shortcut
REM pointing at an app.py that was never written. Check first instead.
set "MISSING="
for %%F in (app.py engine.py fetch.py pipeline.py config.default.toml prompt.md) do (
  if not exist "%%F" set "MISSING=!MISSING! %%F"
)
if defined MISSING (
  echo   [X] The program files are not next to this installer.
  echo       Missing:!MISSING!
  echo.
  echo       This usually means the installer is running from inside the zip.
  echo       Unzip the whole arxiv-digest folder somewhere first - your Desktop
  echo       or Downloads is fine - then open that folder and double-click
  echo       install_windows.bat from there.
  echo.
  pause
  exit /b 1
)

echo   Checking what's on this PC
echo.

REM --- Python 3.11+ ----------------------------------------------------------
REM  Set PYTHON=C:\path\to\python.exe to choose one yourself.
set "PY="
if defined PYTHON if exist "%PYTHON%" (
  "%PYTHON%" -c "import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=%PYTHON%"
)

if not defined PY for %%C in (py.exe python.exe python3.exe) do (
  if not defined PY (
    for /f "delims=" %%P in ('where %%C 2^>nul') do (
      if not defined PY (
        "%%P" -c "import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
        if not errorlevel 1 set "PY=%%P"
      )
    )
  )
)

REM Conda is how most scientists have a modern Python, and it is normally in a
REM named environment that is only on PATH while that environment is active.
REM Double-clicking this file gets a plain cmd.exe with none of that, so look
REM in the environment directories directly - otherwise the one Python on the
REM machine that would work is the one we fail to find.
if not defined PY (
  set "CONDA_ROOTS=%CONDA_PREFIX%;%USERPROFILE%\anaconda3;%USERPROFILE%\miniconda3;%USERPROFILE%\miniforge3;%USERPROFILE%\AppData\Local\anaconda3;%USERPROFILE%\AppData\Local\miniconda3;%USERPROFILE%\AppData\Local\Continuum\anaconda3;C:\ProgramData\anaconda3;C:\ProgramData\miniconda3"
  for %%R in ("!CONDA_ROOTS:;=" "!") do (
    if not defined PY if exist "%%~R\python.exe" (
      "%%~R\python.exe" -c "import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
      if not errorlevel 1 set "PY=%%~R\python.exe"
    )
    if exist "%%~R\envs" for /d %%E in ("%%~R\envs\*") do (
      if not defined PY if exist "%%~E\python.exe" (
        "%%~E\python.exe" -c "import sys;sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
        if not errorlevel 1 set "PY=%%~E\python.exe"
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
  echo       Already have one in a conda environment? Either activate it and
  echo       run this from that prompt, or point at it directly, e.g.
  echo           set PYTHON=%USERPROFILE%\anaconda3\envs\myenv\python.exe
  echo       and run this installer again from the same window.
  echo.
  pause
  exit /b 1
)
REM Via a temp file, not for /f: cmd mis-tokenizes a for /f command that opens
REM with a quoted path, so the version check silently printed nothing at all.
"%PY%" -c "import sys;print(sys.version.split()[0])" > "%TEMP%\_arxiv_pyver.txt" 2>nul
set /p "PYVER=" < "%TEMP%\_arxiv_pyver.txt"
del "%TEMP%\_arxiv_pyver.txt" >nul 2>&1
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

if not exist "%DEST%\examples" mkdir "%DEST%\examples"
copy /Y "examples\*.toml" "%DEST%\examples\" >nul 2>&1

for %%F in (fetch.py record.py pick.py learn.py app.py profile.py engine.py
            configedit.py examples.py pipeline.py schedule.py run.bat learn.bat
            config.default.toml prompt.md learn_prompt.md README.md) do (
  if exist "%%F" copy /Y "%%F" "%DEST%\%%F" >nul
)

if exist "%DEST%\config.toml" (
  echo   [ok] your existing config.toml was left untouched
) else (
  copy /Y "config.default.toml" "%DEST%\config.toml" >nul
  echo   [ok] config.toml created from the defaults
)

REM Say it landed only if it actually landed: copy errors above are sent to nul
REM so the install does not stop on one unreadable file, which also means a
REM wholesale failure would otherwise be announced as a success.
if not exist "%DEST%\app.py" (
  echo   [X] copying the program into %DEST% failed.
  echo       Check that you can write to that folder, then try again.
  echo.
  pause
  exit /b 1
)
echo   [ok] program files installed
echo.

REM --- launcher + Desktop shortcut ------------------------------------------
> "%DEST%\arXiv Digest.bat" echo @echo off
>> "%DEST%\arXiv Digest.bat" echo cd /d "%%~dp0"
>> "%DEST%\arXiv Digest.bat" echo start "" "%PY%" app.py

REM pythonw.exe runs without a console window, so the app opens with no black
REM box behind it. py.exe has its own windowless twin, pyw.exe - without this
REM the substitution below leaves py.exe unchanged, that path exists, and the
REM shortcut keeps a console open for as long as the app runs.
set "PYW=%PY:python.exe=pythonw.exe%"
if /i "!PYW!"=="%PY%" set "PYW=%PY:py.exe=pyw.exe%"
if not exist "!PYW!" for /f "delims=" %%W in ('where pythonw.exe 2^>nul') do set "PYW=%%W"
if not exist "!PYW!" set "PYW=%PY%"
REM powershell.exe by full path: it is not always on PATH, and without it
REM the Desktop lookup and the shortcut below both silently do nothing.
set "PWSH=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PWSH%" set "PWSH=powershell"


REM Ask Windows for the real Desktop folder rather than assuming
REM %USERPROFILE%\Desktop - OneDrive Known Folder Move relocates it to
REM %USERPROFILE%\OneDrive\Desktop, and checking the wrong path makes a
REM shortcut that was created just fine look like it failed.
REM Via a temp file again: a for /f whose command starts with a quoted path
REM is mis-tokenized by cmd, the same trap as the version check above.
"%PWSH%" -NoProfile -Command "[Environment]::GetFolderPath('Desktop')" > "%TEMP%\_arxiv_desktop.txt" 2>nul
set /p "DESKTOP=" < "%TEMP%\_arxiv_desktop.txt"
del "%TEMP%\_arxiv_desktop.txt" >nul 2>&1
if not defined DESKTOP set "DESKTOP=%USERPROFILE%\Desktop"

REM Build the shortcut from a generated .ps1 rather than an inline -Command:
REM escaping quotes through cmd into PowerShell is the single most fragile
REM thing in this script, and a temp file sidesteps it entirely.
set "PS1=%TEMP%\arxiv_digest_shortcut.ps1"
> "%PS1%" echo $w = New-Object -ComObject WScript.Shell
>> "%PS1%" echo $lnk = $w.CreateShortcut('%DESKTOP%\arXiv Digest.lnk')
>> "%PS1%" echo $lnk.TargetPath = '%PYW%'
>> "%PS1%" echo $lnk.Arguments = '"%DEST%\app.py"'
>> "%PS1%" echo $lnk.WorkingDirectory = '%DEST%'
>> "%PS1%" echo $lnk.Description = 'arXiv nightly digest'
>> "%PS1%" echo $lnk.Save()
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%PS1%" >nul 2>&1
del "%PS1%" >nul 2>&1

if exist "%DESKTOP%\arXiv Digest.lnk" (
  echo   [ok] "arXiv Digest" shortcut is on your Desktop
) else (
  echo   [x] could not make a Desktop shortcut - no problem, start it with:
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
    echo   [x] could not schedule it; the app still works
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
