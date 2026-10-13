#!/usr/bin/env pwsh
# Ensure we are in the project root directory
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

# Install Python dependencies (idempotent if already satisfied)
Write-Host "Installing Python dependencies..."
& ".\.venv\Scripts\pip.exe" install -r requirements.txt

# Check if Docker CLI is available
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Write-Warning "Docker CLI not found. Skipping Docker Compose step."
} else {
    # Verify Docker daemon is running
    try {
        docker version | Out-Null
    } catch {
        Write-Error "Docker daemon is not running. Please start Docker Desktop and retry."
        exit 1
    }
    Write-Host "Starting Redis via Docker Compose..."
    docker compose up -d
}

# Start ARQ worker in background
Start-Process -NoNewWindow -FilePath "$projectRoot\.venv\Scripts\python.exe" -ArgumentList "-m","arq","src.worker.WorkerSettings"

# Run the bot using the module entry point
& "$projectRoot\.venv\Scripts\python.exe" -m src.main
