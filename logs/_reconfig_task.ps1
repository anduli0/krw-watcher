$ErrorActionPreference = 'Stop'
$log = 'C:\Users\andul\krw-watcher\logs\reconfig.log'
function W($m){ "$([DateTime]::Now.ToString('HH:mm:ss')) $m" | Out-File -FilePath $log -Append -Encoding utf8 }
try {
  New-Item -ItemType Directory -Force -Path 'C:\Users\andul\krw-watcher\logs' | Out-Null
  Set-Content -Path $log -Value '' -Encoding utf8
  W '=== reconfig start ==='
  W ('elevated as: ' + [Security.Principal.WindowsIdentity]::GetCurrent().Name)
  $taskName = 'KRW-Watcher Public'

  # 1. backup existing task definition
  try {
    Export-ScheduledTask -TaskName $taskName | Out-File 'C:\Users\andul\krw-watcher\logs\KRW-Watcher-Public.task-backup.xml' -Encoding utf8
    W 'backed up existing task XML'
  } catch { W ('no backup: ' + $_.Exception.Message) }

  # 2. stop current task (old interactive launcher)
  try { Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop; W 'stopped old task' } catch { W ('stop: ' + $_.Exception.Message) }
  Start-Sleep -Seconds 2

  # 3. kill any orphaned process still holding 8010
  $pids = Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
  foreach($p in $pids){ try { Stop-Process -Id $p -Force -ErrorAction Stop; W ('killed pid ' + $p + ' on 8010') } catch { W ('kill ' + $p + ': ' + $_.Exception.Message) } }
  Start-Sleep -Seconds 1

  # 4. re-register: SYSTEM account, AtStartup, robust restart (mirrors FedWatcher-Server)
  $action    = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c "C:\Users\andul\krw-watcher\_krw-system-server.bat"' -WorkingDirectory 'C:\Users\andul\krw-watcher'
  $trigger   = New-ScheduledTaskTrigger -AtStartup
  $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
  $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'KRW-Watcher public server on 127.0.0.1:8010 (Tailscale Funnel root URL). Runs as SYSTEM AtStartup; survives reboot and logout. Launcher: _krw-system-server.bat' -Force | Out-Null
  W 'registered new SYSTEM / AtStartup task'

  # force PT0S (no execution time limit) directly, in case the cmdlet defaulted it
  try {
    $t2 = Get-ScheduledTask -TaskName $taskName
    $t2.Settings.ExecutionTimeLimit = 'PT0S'
    Set-ScheduledTask -TaskName $taskName -Settings $t2.Settings | Out-Null
    W 'forced ExecutionTimeLimit=PT0S'
  } catch { W ('execlimit set: ' + $_.Exception.Message) }

  # 5. start it now
  Start-ScheduledTask -TaskName $taskName
  W 'started task'

  # 6. read back the resulting definition for verification
  $t = Get-ScheduledTask -TaskName $taskName
  W ('RESULT principal=' + $t.Principal.UserId + '/' + $t.Principal.LogonType + '/' + $t.Principal.RunLevel)
  W ('RESULT trigger=' + (($t.Triggers | ForEach-Object { $_.CimClass.CimClassName }) -join ','))
  W ('RESULT execLimit=' + $t.Settings.ExecutionTimeLimit + ' restartCount=' + $t.Settings.RestartCount + ' restartInterval=' + $t.Settings.RestartInterval + ' disallowOnBattery=' + $t.Settings.DisallowStartIfOnBatteries)
  W ('RESULT state=' + $t.State)
  W '=== reconfig done OK ==='
} catch {
  W ('FATAL: ' + $_.Exception.Message)
}
