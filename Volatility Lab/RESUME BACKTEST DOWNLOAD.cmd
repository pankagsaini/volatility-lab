@echo off
title Volatility Lab - Historical Download
cd /d "%~dp0"
if not exist ".runtime\python.exe" (
 echo Bundled Python is missing. Keep this shortcut inside the Volatility Lab folder.
 pause
 exit /b 1
)
".runtime\python.exe" -X utf8 "download_control.py" %*
echo.
echo You can close this window. An active download will keep running.
pause
