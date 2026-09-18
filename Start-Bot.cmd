@echo off
setlocal
cd /d "%~dp0"
title LinkedIn Easy Apply
echo.
echo LinkedIn Easy Apply
echo Project: %CD%
echo.

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. From this folder run:
  echo   python -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -e ".[dev]"
  echo.
  pause
  exit /b 1
)

if /I "%~1"=="login" (
  echo Opening Firefox so you can log into LinkedIn...
  ".venv\Scripts\python.exe" -u -m linkedin_easy_apply --login
  echo.
  pause
  exit /b %ERRORLEVEL%
)

if /I "%~1"=="check" (
  ".venv\Scripts\python.exe" -u -m linkedin_easy_apply --check
  echo.
  pause
  exit /b %ERRORLEVEL%
)

echo Starting the bot.
echo 1. Close Firefox windows that belong to this bot.
echo 2. Keep the new Firefox window visible.
echo 3. Press Ctrl+C in this window to stop.
echo.
".venv\Scripts\python.exe" -u -m linkedin_easy_apply %*
set EXITCODE=%ERRORLEVEL%
echo.
echo Bot finished with exit code %EXITCODE%.
pause
exit /b %EXITCODE%
