# Starts FastAPI. FastAPI launches both MCP servers automatically (stdio subprocesses).
# Run from the project root:   .\start-backend.ps1
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\backend"
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
  Write-Host "Creating virtual environment..."
  python -m venv .venv
  .\.venv\Scripts\python.exe -m pip install --upgrade pip
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
