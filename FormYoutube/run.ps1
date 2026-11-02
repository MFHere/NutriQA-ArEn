# YouTube CSV Processor - Start both FastAPI and Streamlit
# Usage: .\run.ps1

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host "Starting YouTube CSV Processor..." -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan

# Load environment variables from .env if it exists
if (Test-Path ".env") {
    Write-Host "Loading environment variables..." -ForegroundColor Green
    Get-Content .env | Where-Object { $_ -notmatch '^\s*#' -and $_.Trim() } | ForEach-Object {
        $parts = $_ -split '=', 2
        if ($parts.Count -eq 2) {
            [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
        }
    }
}

# Start FastAPI backend in background
Write-Host "Starting FastAPI backend on http://127.0.0.1:8000..." -ForegroundColor Green
Start-Process -FilePath python -ArgumentList "-m uvicorn main:app --host 127.0.0.1 --port 8000" -WindowStyle Minimized -WorkingDirectory "$scriptDir\backend"

# Wait for FastAPI to start
Start-Sleep -Seconds 3

# Start Streamlit in background
Write-Host "Starting Streamlit UI on http://127.0.0.1:8501..." -ForegroundColor Green
Start-Process -FilePath streamlit -ArgumentList "run app.py --server.port 8501 --server.address 127.0.0.1" -WindowStyle Minimized -WorkingDirectory $scriptDir

Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "✓ FastAPI started on http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "✓ Streamlit started on http://127.0.0.1:8501" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""
Write-Host "Both services are running in background windows" -ForegroundColor Yellow
