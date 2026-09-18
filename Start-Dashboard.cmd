@echo off
setlocal
cd /d "%~dp0"
title Easy Apply console
echo.
echo Easy Apply console
echo Opens http://127.0.0.1:8787
echo From the console: Start run = Ollama + worker. Login opens LinkedIn.
echo You do not need Start-Bot.cmd or Start-Ollama.cmd after this window is open.
echo Close this window to stop the dashboard.
echo.

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. From this folder run:
  echo   python -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -e ".[dev]"
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -u -m linkedin_easy_apply --dashboard
set EXITCODE=%ERRORLEVEL%
echo.
echo Dashboard stopped with exit code %EXITCODE%.
pause
exit /b %EXITCODE%
