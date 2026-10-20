# Registers the SwingBot jobs in Windows Task Scheduler (folder "SwingBot"). Local time is Central.
#   powershell -ExecutionPolicy Bypass -File scheduler\install_tasks.ps1            install / replace
#   powershell -ExecutionPolicy Bypass -File scheduler\install_tasks.ps1 -Remove    delete them
param([switch]$Remove)
$script = Join-Path $PSScriptRoot 'run_bot.ps1'
$jobs = @(
    @{ Name = 'Swing monitor (stops)'; Job = 'monitor'; At = '11:00AM'; Days = 'Monday,Tuesday,Wednesday,Thursday,Friday' },
    @{ Name = 'Swing scan (entries)';  Job = 'scan';    At = '2:45PM';  Days = 'Monday,Tuesday,Wednesday,Thursday,Friday' },
    @{ Name = 'Rotation monthly';      Job = 'rotate';  At = '9:35AM';  Days = $null }   # daily trigger; the script only acts on days 1-5
)
foreach ($j in $jobs) {
    $path = '\SwingBot\'
    if ($Remove) { Unregister-ScheduledTask -TaskName $j.Name -TaskPath $path -Confirm:$false -ErrorAction SilentlyContinue; continue }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Job $($j.Job)"
    if ($j.Days) { $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek ($j.Days -split ',') -At $j.At }
    else { $trigger = New-ScheduledTaskTrigger -Daily -At $j.At }
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
    # Interactive logon: needs you signed in, because the tastytrade credentials live in YOUR Credential Manager.
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $j.Name -TaskPath $path -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Output "Registered $path$($j.Name)  ($($j.Job) at $($j.At))"
}
if ($Remove) { Write-Output 'SwingBot tasks removed.' }
