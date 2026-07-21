# KRW-Watcher launcher (Windows / PowerShell)
# Runs FULLY INDEPENDENTLY of Fed-Watcher: own folder, own .venv, own port (8010), own DB.
# Fed-Watcher lives at C:\Users\andul\fed-watcher on port 8000 and is never touched here.
#
# Usage:  .\run.ps1              # start the API server (frees its own port first)
#         .\run.ps1 -Once        # run a single forecast cycle in the terminal and exit
#         .\run.ps1 -Port 8011   # use a different port
param([switch]$Once, [int]$Port = 8010)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtualenv (.venv)..." -ForegroundColor Cyan
    python -m venv .venv
}
# Always use THIS project's own venv python — guarantees isolation from Fed-Watcher.
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Write-Host "Installing dependencies..." -ForegroundColor Cyan
& $py -m pip install -q -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from template - add your ANTHROPIC_API_KEY and FRED_API_KEY." -ForegroundColor Yellow
}

if ($Once) {
    & $py run_once.py
    return
}

# Free ONLY this app's port if a stale KRW-Watcher process is still holding it.
# (`python -m uvicorn` can leave a process behind on Windows — this prevents the
# "suddenly won't start / address already in use" failure.) Port 8000 is never touched.
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    $pids = $busy.OwningProcess | Select-Object -Unique
    Write-Host "Port $Port is busy (PID $($pids -join ',')) - freeing it for a clean start..." -ForegroundColor Yellow
    $pids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

Write-Host "Starting KRW-Watcher on http://localhost:$Port" -ForegroundColor Green
Write-Host "  (first startup takes ~15-20s while 23 agents + clients load - please wait)" -ForegroundColor DarkGray
Write-Host "  Fed-Watcher (port 8000) is a separate app and runs independently." -ForegroundColor DarkGray
& $py -m uvicorn backend.main:app --host 0.0.0.0 --port $Port
