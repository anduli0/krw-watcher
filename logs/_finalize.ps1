$ErrorActionPreference = 'Continue'
$log = 'C:\Users\andul\krw-watcher\logs\restart.log'
function W($m){ "$([DateTime]::Now.ToString('HH:mm:ss')) FINAL $m" | Out-File -FilePath $log -Append -Encoding utf8 }
$task = 'KRW-Watcher Public'
W ('start; state=' + (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue).State)

# 1. clean slate: stop task, kill the watchdog bat (incl. elevated fallback) + free 8010
try { Stop-ScheduledTask -TaskName $task } catch {}
Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like '*_krw-system-server*' } |
  ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force; W ('killed bat cmd ' + $_.ProcessId) } catch {} }
Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { try { Stop-Process -Id $_ -Force; W ('killed 8010 pid ' + $_) } catch {} }
Start-Sleep -Seconds 3

# 2. re-register the boot task as a FIRE-AND-FORGET launcher (fixes the stuck "Queued":
#    the old task ran the bat in the foreground, so a manual stop/start left a lingering
#    instance that IgnoreNew queued behind. The launcher exits in ~1s, leaving the detached
#    watchdog bat running — no long-lived task instance to get stuck.)
$action    = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\andul\krw-watcher\start_krw_watcher.ps1"' -WorkingDirectory 'C:\Users\andul\krw-watcher'
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances Parallel
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'KRW-Watcher boot launcher (SYSTEM, AtStartup): runs start_krw_watcher.ps1 which starts the idempotent _krw-system-server.bat watchdog on 127.0.0.1:8010 if not already up. Pairs with the Startup-folder krw-watcher.vbs (logon).' -Force | Out-Null
W ('re-registered as launcher; state=' + (Get-ScheduledTask -TaskName $task).State)

# 3. start it now -> verifies the BOOT path (launcher -> watchdog -> 8010), no longer foreground
Start-ScheduledTask -TaskName $task
Start-Sleep -Seconds 5
W ('post-start state=' + (Get-ScheduledTask -TaskName $task).State)
function Up { try { return (Invoke-WebRequest 'http://127.0.0.1:8010/health' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { return $false } }
$up = $false
for ($i = 0; $i -lt 40; $i++) { if (Up) { $up = $true; W ('health 200 via TASK launcher after ~' + ($i*3) + 's'); break }; Start-Sleep -Seconds 3 }

# 4. fallback: if the task path somehow did not serve, launch the watchdog directly
if (-not $up) {
  W 'task launcher did not bring up 8010 — launching watchdog directly'
  Start-Process -WindowStyle Hidden -FilePath 'cmd.exe' -ArgumentList '/c','C:\Users\andul\krw-watcher\_krw-system-server.bat' -WorkingDirectory 'C:\Users\andul\krw-watcher'
  for ($i = 0; $i -lt 40; $i++) { if (Up) { $up = $true; W ('health 200 via direct watchdog after ~' + ($i*3) + 's'); break }; Start-Sleep -Seconds 3 }
}

# 5. report owner + live numbers
if ($up) {
  $p = Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | Select-Object -First 1
  $o = $null; try { $o = (Get-CimInstance Win32_Process -Filter "ProcessId=$p" | Invoke-CimMethod -MethodName GetOwner) } catch {}
  W ('8010 pid=' + $p + ' owner=' + $o.Domain + '\' + $o.User)
  try { $j = (Invoke-WebRequest 'http://127.0.0.1:8010/api/accuracy/simulation' -UseBasicParsing -TimeoutSec 30).Content | ConvertFrom-Json
        foreach ($h in '1w','1m') { $hz=$j.horizons.$h; if($hz){ W ('  '+$h+' indep_hit='+$hz.independent_hit+' n='+$hz.independent_n) } } } catch {}
}
W ('DONE up=' + $up + ' taskState=' + (Get-ScheduledTask -TaskName $task).State)
