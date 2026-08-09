@echo off
title Alpha Command DataDesk

cd /d "%~dp0"

echo [Alpha Command] Initializing DataDesk...
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
    echo Installing dependencies...
    .venv\Scripts\python -m pip install -q -e .[dev]
)

echo.
echo =========================================
echo Running Core Backtest...
echo =========================================
.venv\Scripts\python main.py backtest

echo.
echo =========================================
echo Running Blended Holdout Report...
echo =========================================
.venv\Scripts\python main.py holdout

echo.
echo =========================================
echo Killing existing Ops Console (Port 8000)...
echo =========================================
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000') do (
    if "%%a" neq "0" (
        taskkill /F /PID %%a >nul 2>&1
    )
)

echo.
echo =========================================
echo Starting Ops Console...
echo =========================================
start http://localhost:8000
.venv\Scripts\python main.py serve

pause
