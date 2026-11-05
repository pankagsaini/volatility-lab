@echo off
set "SCRIPT_DIR=%~dp0"
if exist "%SCRIPT_DIR%Volatility Lab\.runtime\pythonw.exe" (
    start "" "%SCRIPT_DIR%Volatility Lab\.runtime\pythonw.exe" "%SCRIPT_DIR%Volatility Lab\desktop_web.py"
) else (
    start "" python "%SCRIPT_DIR%run_local.py" --web
)
