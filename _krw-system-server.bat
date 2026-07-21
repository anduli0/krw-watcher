@echo off
REM KRW-Watcher server watchdog — serves uvicorn on 127.0.0.1:8010 (Tailscale Funnel root URL).
REM IDEMPOTENT + self-healing: if 8010 is already being served (another launcher/instance owns
REM it) this idles instead of competing; otherwise it (re)starts uvicorn and keeps it alive. That
REM makes it safe to be launched from BOTH the Startup-folder logon .vbs AND a boot task without
REM ever double-binding the port. Uses the FULL venv python path so it also works under SYSTEM
REM (which has no per-user 'python' alias). Serves port 8010 ONLY — never touches Fed 8000 /
REM us-watcher 8088 / kospi 18080. Self-heal uses goto (NOT an if() block): a parenthesized
REM block around the PowerShell one-liners' own parens breaks cmd parsing and exits the bat.
cd /d "%~dp0"

set "PYEXE=C:\Users\andul\krw-watcher\.venv\Scripts\python.exe"
set "DEV_MODE=false"
set "ALLOWED_IPS=*"
set "OWNER_MAC="
set "BROKER=paper"
set "ENABLE_LIVE_TRADING=false"
if not exist "%~dp0logs" mkdir "%~dp0logs"

:loop
REM already healthy? (another launcher/instance owns 8010) -> idle, do not start a 2nd uvicorn
powershell -NoProfile -Command "try{ if((Invoke-WebRequest 'http://127.0.0.1:8010/health' -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200){exit 0} }catch{}; exit 1"
if not errorlevel 1 goto idle
REM not serving -> reclaim any stale 8010 listener, then run uvicorn in the FOREGROUND (blocks)
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"
"%PYEXE%" -m uvicorn backend.main:app --host 127.0.0.1 --port 8010 --no-use-colors
ping -n 4 127.0.0.1 >nul
goto loop
:idle
timeout /t 30 /nobreak >nul
goto loop
