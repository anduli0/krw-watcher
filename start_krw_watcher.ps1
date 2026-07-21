# KRW-WATCHER launcher — starts the 8010 server watchdog if it is not already up.
# Idempotent: a duplicate logon/boot trigger is a no-op when 8010 already serves.
# Invoked at logon by the Startup-folder krw-watcher.vbs (mirrors kospi/us-watcher).
$ErrorActionPreference = "SilentlyContinue"
$root = "C:\Users\andul\krw-watcher"
Set-Location $root

function Test-Port([int]$p) {
  [bool](Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)
}

# Only start the watchdog if nothing is serving 8010 yet (the bat self-heals from there).
if (-not (Test-Port 8010)) {
  Start-Process -WindowStyle Hidden -FilePath "cmd.exe" `
    -ArgumentList '/c', (Join-Path $root "_krw-system-server.bat") -WorkingDirectory $root
}
