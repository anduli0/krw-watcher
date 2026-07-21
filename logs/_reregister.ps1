$ErrorActionPreference = 'Continue'
$log = 'C:\Users\andul\krw-watcher\logs\restart.log'
function W($m){ "$([DateTime]::Now.ToString('HH:mm:ss')) REREG $m" | Out-File -FilePath $log -Append -Encoding utf8 }
$task = 'KRW-Watcher Public'
W ('start; state=' + (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue).State)

# 1. free port 8010: stop task, kill the watchdog bat (incl. the elevated fallback) + uvicorn
try { Stop-ScheduledTask -TaskName $task } catch {}
Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like '*_krw-system-server*' } |
  ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force; W ('killed bat cmd ' + $_.ProcessId) } catch {} }
Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { try { Stop-Process -Id $_ -Force; W ('killed 8010 pid ' + $_) } catch {} }
Start-Sleep -Seconds 3

# 2. re-register FRESH (clears any stuck scheduler instance tracking) — same config as 10:11
$action    = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c "C:\Users\andul\krw-watcher\_krw-system-server.bat"' -WorkingDirectory 'C:\Users\andul\krw-watcher'
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'KRW-Watcher public server on 127.0.0.1:8010 (Tailscale Funnel root). SYSTEM AtStartup; survives reboot and logout. Launcher: _krw-system-server.bat' -Force | Out-Null
try { $s = Get-ScheduledTask -TaskName $task; $s.Settings.ExecutionTimeLimit = 'PT0S'; Set-ScheduledTask -TaskName $task -Settings $s.Settings | Out-Null } catch {}
W ('re-registered; state=' + (Get-ScheduledTask -TaskName $task).State)

# 3. start and verify it goes Running + binds 8010
Start-ScheduledTask -TaskName $task
Start-Sleep -Seconds 6
W ('post-start state=' + (Get-ScheduledTask -TaskName $task).State)
function Up { try { return (Invoke-WebRequest 'http://127.0.0.1:8010/health' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { return $false } }
$up = $false
for ($i = 0; $i -lt 40; $i++) { if (Up) { $up = $true; W ('health 200 via TASK after ~' + ($i*3) + 's; state=' + (Get-ScheduledTask -TaskName $task).State); break }; Start-Sleep -Seconds 3 }

# 4. fallback so service is never left down
if (-not $up) {
  W ('task still not serving (state=' + (Get-ScheduledTask -TaskName $task).State + ') — relaunching bat directly')
  Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','C:\Users\andul\krw-watcher\_krw-system-server.bat' -WorkingDirectory 'C:\Users\andul\krw-watcher' -WindowStyle Hidden
  for ($i = 0; $i -lt 40; $i++) { if (Up) { $up = $true; W ('health 200 via fallback after ~' + ($i*3) + 's'); break }; Start-Sleep -Seconds 3 }
}

# 5. confirm 8010 owner identity (SYSTEM if the task owns it) + new numbers
if ($up) {
  $pid8010 = Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | Select-Object -First 1
  $owner = $null; try { $owner = (Get-CimInstance Win32_Process -Filter "ProcessId=$pid8010" | Invoke-CimMethod -MethodName GetOwner) } catch {}
  W ('8010 pid=' + $pid8010 + ' owner=' + ($owner.Domain) + '\' + ($owner.User))
  try { $j = (Invoke-WebRequest 'http://127.0.0.1:8010/api/accuracy/simulation' -UseBasicParsing -TimeoutSec 30).Content | ConvertFrom-Json
        foreach ($h in '1w','1m') { $hz=$j.horizons.$h; if($hz){ W ('  '+$h+' indep_hit='+$hz.independent_hit+' n='+$hz.independent_n) } } } catch {}
}
W ('DONE up=' + $up + ' finalState=' + (Get-ScheduledTask -TaskName $task).State)
