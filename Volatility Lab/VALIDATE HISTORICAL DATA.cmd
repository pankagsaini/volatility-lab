@echo off
cd /d "%~dp0"
set "PYTHONPATH=%~dp0.dependencies"
echo Checking completed historical minute files. Original data is not changed.
echo Progress: data\audit\candles\status.json
"%~dp0.runtime\python.exe" "%~dp0candle_validation.py"
echo Reports: data\audit\candles
pause
