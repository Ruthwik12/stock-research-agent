# Registers (or re-registers) a Windows scheduled task that runs scripts/update_daily.py every day.
# Runs as the current user only while logged on (no stored password). If the laptop is off or
# asleep at the scheduled time, it runs as soon as possible afterwards.
#
#   powershell -ExecutionPolicy Bypass -File scripts\schedule_daily_update.ps1            # register
#   Unregister-ScheduledTask -TaskName StockAgent-DailyFilingsUpdate -Confirm:$false      # remove
#   Start-ScheduledTask -TaskName StockAgent-DailyFilingsUpdate                           # run now

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$script = Join-Path $root "scripts\update_daily.py"
$taskName = "StockAgent-DailyFilingsUpdate"

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $root
# 11:55 PM local: after US market close and evening earnings 8-Ks, and after the evening's manual use of the app.
$trigger = New-ScheduledTaskTrigger -Daily -At "11:55PM"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Stock research agent: download new SEC filings, parse, and index them." -Force | Out-Null

Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime
