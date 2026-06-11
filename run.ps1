# DataDesk one-command run: sets up the venv if needed, runs the core backtest,
# and starts the ops console on http://localhost:8000
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) {
    Write-Host "Creating venv + installing (first run only)..."
    python -m venv .venv
    .venv\Scripts\python -m pip install -q -e .[dev]
}

Write-Host "Running core backtest..."
.venv\Scripts\python main.py backtest

Write-Host "Starting ops console at http://localhost:8000 (Ctrl+C to stop)"
Start-Process "http://localhost:8000"
.venv\Scripts\python main.py serve
