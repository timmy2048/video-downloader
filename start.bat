@echo off
REM Video Downloader - one-click start (Windows)
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3 not found. Install it from https://www.python.org/downloads/
  echo         and check "Add python.exe to PATH" during installation.
  pause
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  echo ^>^>^> Creating virtual environment venv ...
  %PY% -m venv venv
  if errorlevel 1 (
    echo [ERROR] Failed to create venv.
    pause
    exit /b 1
  )
)

echo ^>^>^> Installing / updating dependencies (incl. latest yt-dlp) ...
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -U -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Failed to install dependencies. Check your network and try again.
  pause
  exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [NOTE] ffmpeg not found: single-file formats will be used (lower quality, no MP3^).
  echo        Install with: winget install Gyan.FFmpeg   then restart this script.
)

set "PYTHONUTF8=1"
set "OPEN_BROWSER=1"
"venv\Scripts\python.exe" app.py
pause
