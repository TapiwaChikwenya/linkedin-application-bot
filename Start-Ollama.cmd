@echo off
setlocal
cd /d "%~dp0"
title Local Llama / Ollama
echo.
echo Local Llama sidecar for Easy Apply
echo.
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama is not installed.
  echo Install it from https://ollama.com/download then run this file again.
  echo.
  pause
  exit /b 1
)

echo Pulling llama3.2 if it is not already present...
ollama pull llama3.2
if errorlevel 1 (
  echo Could not pull llama3.2. Is Ollama running?
  echo.
  pause
  exit /b 1
)

echo.
echo Ollama model ready: llama3.2
echo Leave Ollama running. The Easy Apply worker will use it automatically
echo when LINKEDIN_LLM=auto in .env.
echo Optional vision model: ollama pull llama3.2-vision
echo then set OLLAMA_MODEL=llama3.2-vision
echo.
pause
exit /b 0
