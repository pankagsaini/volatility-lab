@echo off
set "SCRIPT_DIR=%~dp0"
if exist "%SCRIPT_DIR%Volatility Lab\.runtime\pythonw.exe" (
    "%SCRIPT_DIR%Volatility Lab\.runtime\pythonw.exe" "%SCRIPT_DIR%Volatility Lab\desktop.py" --stop
) else (
    python "%SCRIPT_DIR%Volatility Lab\desktop.py" --stop
)
