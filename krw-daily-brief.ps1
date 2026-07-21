# KRW-Watcher daily brief — EXTERNAL trigger that runs a FRESH cycle+brief from DISK code.
#
# Independent of BOTH failure modes we hit:
#   1. the in-process APScheduler silently dying (HTTP stays healthy → watchdog never restarts it);
#   2. the always-on uvicorn serving STALE code (it's elevated/SYSTEM, non-admin can't restart it).
# By invoking daily_cycle_brief.py with the venv python, the daily forecast + brief ALWAYS use the
# latest on-disk logic (incl. the desk structural view) and push to Telegram directly — no API,
# no dependency on the running server's code version. daily_cycle_brief.py is idempotent (skips if
# today's brief already exists), so the 08:30 daily trigger and the at-logon safety net never double up.
$ErrorActionPreference = "SilentlyContinue"
$root   = "C:\Users\andul\krw-watcher"
$py     = Join-Path $root ".venv\Scripts\python.exe"
$script = Join-Path $root "daily_cycle_brief.py"
$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir "daily-brief.log"

function Write-Log([string]$msg) {
  ((Get-Date).ToString("yyyy-MM-dd HH:mm:ss") + "  " + $msg) | Out-File -FilePath $log -Append -Encoding utf8
}

Write-Log "=== daily-brief run start ==="
if (-not (Test-Path $py))     { Write-Log "ABORT: venv python not found at $py"; exit 1 }
if (-not (Test-Path $script)) { Write-Log "ABORT: $script not found"; exit 1 }

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
Set-Location $root

# Run the fresh-process cycle + brief; capture stdout/stderr into the log.
$out = & $py $script 2>&1
foreach ($line in $out) { if ("$line".Trim()) { Write-Log ("py: " + $line) } }
$code = $LASTEXITCODE
Write-Log "=== daily-brief run done (exit $code) ==="
exit $code
