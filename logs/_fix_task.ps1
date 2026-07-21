$ErrorActionPreference = 'Continue'
$log = 'C:\Users\andul\krw-watcher\logs\restart.log'
function W($m){ "$([DateTime]::Now.ToString('HH:mm:ss')) FIX $m" | Out-File -FilePath $log -Append -Encoding utf8 }
$task = 'KRW-Watcher Public'
W ('start; state=' + (Get-ScheduledTask -TaskName $task).State)

# 1. fully stop + clear any stuck/terminating instance
try { Stop-ScheduledTask -TaskName $task } catch { W ('stop: ' + $_.Exception.Message) }
Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like '*_krw-system-server*' } |
  ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force; W ('killed bat cmd ' + $_.ProcessId) } catch {} }
Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { try { Stop-Process -Id $_ -Force; W ('killed 8010 pid ' + $_) } catch {} }
Start-Sleep -Seconds 4

# 2. wait until the task settles to Ready
for ($i = 0; $i -lt 12; $i++) {
  if ((Get-ScheduledTask -TaskName $task).State -eq 'Ready') { break }
  Start-Sleep -Seconds 2
}
W ('pre-start state=' + (Get-ScheduledTask -TaskName $task).State)

# 3. start it
try { Start-ScheduledTask -TaskName $task } catch { W ('start: ' + $_.Exception.Message) }
Start-Sleep -Seconds 6
W ('post-start state=' + (Get-ScheduledTask -TaskName $task).State)

# 4. poll health up to ~120s
function Up { try { return (Invoke-WebRequest 'http://127.0.0.1:8010/health' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { return $false } }
$up = $false
for ($i = 0; $i -lt 40; $i++) { if (Up) { $up = $true; W ('health 200 via task after ~' + ($i*3) + 's'); break }; Start-Sleep -Seconds 3 }

# 5. fallback: if the task still didn't bind 8010, launch the watchdog bat directly
if (-not $up) {
  W 'task did not bind 8010 — launching _krw-system-server.bat directly (fallback)'
  Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','C:\Users\andul\krw-watcher\_krw-system-server.bat' -WorkingDirectory 'C:\Users\andul\krw-watcher' -WindowStyle Hidden
  for ($i = 0; $i -lt 40; $i++) { if (Up) { $up = $true; W ('health 200 via fallback after ~' + ($i*3) + 's'); break }; Start-Sleep -Seconds 3 }
}

# 6. confirm new accuracy numbers if up
if ($up) {
  try {
    $j = (Invoke-WebRequest 'http://127.0.0.1:8010/api/accuracy/simulation' -UseBasicParsing -TimeoutSec 30).Content | ConvertFrom-Json
    foreach ($h in '1w','1m') { $hz = $j.horizons.$h; if ($hz) { W ('  ' + $h + ' indep_hit=' + $hz.independent_hit + ' n=' + $hz.independent_n + ' ic=' + $hz.ic) } }
  } catch { W ('api: ' + $_.Exception.Message) }
  W ('DONE up=true finalState=' + (Get-ScheduledTask -TaskName $task).State)
} else {
  W 'DONE up=FALSE — still down'
}
