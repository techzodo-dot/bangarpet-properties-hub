@echo off
REM Run the site locally on Windows: double-click run_local.bat (or run it in a terminal).
REM Creates a virtual environment, installs dependencies, sets up the database,
REM creates the admin login from ADMIN_USERNAME / ADMIN_PASSWORD in .env and
REM starts the site at http://127.0.0.1:8000/
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/
  echo During installation, tick "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv" (
  echo ==^> Creating virtual environment (.venv)
  %PY% -m venv .venv || goto :error
)
set "VPY=.venv\Scripts\python.exe"

echo ==^> Installing dependencies
"%VPY%" -m pip install --quiet --upgrade pip || goto :error
"%VPY%" -m pip install --quiet -r requirements.txt || goto :error

if not exist ".env" (
  echo ==^> Creating .env from .env.example
  copy /y .env.example .env >nul
)

echo ==^> Setting up the database
"%VPY%" manage.py migrate --verbosity 0 || goto :error
echo ==^> Creating / updating the admin login (ADMIN_USERNAME / ADMIN_PASSWORD in .env)
"%VPY%" manage.py ensure_admin --keep-password --skip-if-missing || goto :error

echo.
echo ============================================================
echo   Bangarpet Property Hub:  http://127.0.0.1:8000/
echo   Admin panel:             http://127.0.0.1:8000/management/  (sign in with ADMIN_USERNAME)
echo   Press Ctrl+C to stop.
echo ============================================================
start "" "http://127.0.0.1:8000/"
"%VPY%" manage.py runserver 127.0.0.1:8000
goto :eof

:error
echo.
echo Something went wrong. Check the messages above.
pause
exit /b 1
