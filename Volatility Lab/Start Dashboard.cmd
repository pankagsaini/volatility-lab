@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.runtime\pythonw.exe" (
  echo The private Python runtime is missing. Restore the complete Volatility Lab folder.
  pause
  exit /b 1
)
start "" "%~dp0.runtime\pythonw.exe" "%~dp0desktop.py"
