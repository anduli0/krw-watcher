$ErrorActionPreference = 'Stop'
$log = 'C:\Users\andul\krw-watcher\logs\restart.log'
function W($m){ "$([DateTime]::Now.ToString('HH:mm:ss')) $m" | Out-File -FilePath $log -Append -Encoding utf8 }
try {
  Set-Content -Path $log -Value '' -Encoding utf8
  W '=== restart krw 8010 (pick up new forecast_sim) ==='
  $task = 'KRW-Watcher Public'
  # 1. stop the SYSTEM task (kills its uvicorn child on 8010 ONLY; never touches 8000/8088/18080)
  try { Stop-ScheduledTask -TaskName $task; W 'stopped task' } catch { W ('stop: ' + $_.Exception.Message) }
  Start-Sleep -Seconds 2
  # 2. reap any process still bound to 8010 (target by PORT, per ops note)
  $pids = Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue |
          Select-Object -ExpandProperty OwningProcess -Unique
  foreach($p in $pids){ try { Stop-Process -Id $p -Force; W ('killed pid ' + $p + ' on 8010') } catch { W ('kill ' + $p + ': ' + $_.Exception.Message) } }
  Start-Sleep -Seconds 1
  # 3. start it again → _krw-system-server.bat reloads forecast_sim with new code
  Start-ScheduledTask -TaskName $task; W 'started task'
  # 4. poll local health up to ~100s
  $up = $false
  for($i=0; $i -lt 33; $i++){
    try { if((Invoke-WebRequest 'http://127.0.0.1:8010/health' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200){ $up=$true; W ('health 200 after ~' + ($i*3) + 's'); break } } catch {}
    Start-Sleep -Seconds 3
  }
  if(-not $up){ W 'NOT UP after ~100s'; return }
  # 5. confirm the NEW accuracy numbers are live via the API
  try {
    $r = Invoke-WebRequest 'http://127.0.0.1:8010/api/accuracy/simulation' -UseBasicParsing -TimeoutSec 30
    $j = $r.Content | ConvertFrom-Json
    foreach($h in '1w','1m','3m','12m'){
      $hz = $j.horizons.$h
      if($hz){ W ('  ' + $h + ' independent_hit=' + $hz.independent_hit + ' n=' + $hz.independent_n + ' ic=' + $hz.ic) }
    }
    W 'API accuracy/simulation OK (new schema live)'
  } catch { W ('api: ' + $_.Exception.Message) }
  W '=== done ==='
} catch { W ('FATAL: ' + $_.Exception.Message) }
