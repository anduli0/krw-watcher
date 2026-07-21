@echo off
title KRW-Watcher server (public via Tailscale Funnel) - close to stop
cd /d "%~dp0"

REM ── Production (secure) mode is pinned in .env; set here too as a belt-and-suspenders. ──
set "DEV_MODE=false"
set "ALLOWED_IPS=*"
set "OWNER_MAC="
set "BROKER=paper"
set "ENABLE_LIVE_TRADING=false"

echo.
echo   ===================================================
echo     KRW-WATCHER server  (public via Tailscale Funnel)
echo   ===================================================
echo   Permanent public URL:  https://krw-watcher.tail3e31a9.ts.net
echo   Admin password:  oSukScK2yA41
echo   Viewing is public; running a cycle / brief needs the admin password.
echo.
echo   This window keeps the 8010 server alive and self-heals it every 30s.
echo   Public exposure is handled by the always-on Tailscale service (fixed URL,
echo   survives reboots). No Cloudflare tunnel needed anymore.
echo   (Old rotating-URL launcher kept as krw-watcher-cloudflare-backup.bat.)
echo.

if not exist "%~dp0logs" mkdir "%~dp0logs"

:loop
REM ── self-heal: restart uvicorn if it ever stops answering (8010 only; never touches 8000) ──
powershell -NoProfile -Command "try{ if((Invoke-WebRequest 'http://127.0.0.1:8010/health' -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200){exit 0} }catch{}; exit 1"
if not errorlevel 1 goto alive
echo   server not answering - (re)starting it...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"
start "KRW-Watcher server" /min cmd /c "cd /d %~dp0 && .venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8010 --no-use-colors"
powershell -NoProfile -Command "for($i=0;$i -lt 120;$i++){ try{ if((Invoke-WebRequest 'http://127.0.0.1:8010/health' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200){break} }catch{}; Start-Sleep 1 }"
:alive
timeout /t 30 /nobreak >nul
goto loop
