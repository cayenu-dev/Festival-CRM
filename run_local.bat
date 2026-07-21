@echo off
REM Festival CRM - run on your own machine (Windows).
REM Double-click this file, then open http://localhost:8000
REM Data is saved in festival_crm.db in this folder.
cd /d "%~dp0"

where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Python 3 isn't installed. Get it from https://www.python.org/downloads/
  echo During install, check "Add Python to PATH", then run this again.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo First run: setting up ^(about a minute^)...
  %PY% -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

start "" http://localhost:8000
echo.
echo Festival CRM is running -^> http://localhost:8000
echo Leave this window open. Press Ctrl+C to stop.
echo.
uvicorn main:app --host 127.0.0.1 --port 8000
