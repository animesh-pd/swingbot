# Registers the SwingBot jobs in Windows Task Scheduler (folder "SwingBot"). Local time is Central.
#   powershell -ExecutionPolicy Bypass -File scheduler\install_tasks.ps1            install / replace
#   powershell -ExecutionPolicy Bypass -File scheduler\install_tasks.ps1 -Remove    delete them
#   ... -WithUnlockTrigger   also start keep-awake when you unlock the PC (needs an administrator terminal)
param([switch]$Remove, [switch]$WithUnlockTrigger)
$script = Join-Path $PSScriptRoot 'run_bot.ps1'
$jobs = @(
    @{ Name = 'Keep awake (market hours)'; Script = 'keep_awake.ps1'; At = '8:20AM'; Days = 'Monday,Tuesday,Wednesday,Thursday,Friday' },
    @{ Name = 'Swing monitor (stops)'; Job = 'monitor'; At = '11:00AM'; Days = 'Monday,Tuesday,Wednesday,Thursday,Friday' },
    @{ Name = 'Swing scan (entries)';  Job = 'scan';    At = '2:45PM';  Days = 'Monday,Tuesday,Wednesday,Thursday,Friday' },
    @{ Name = 'Weekly summary';        Job = 'weekly';  At = '3:10PM';  Days = 'Thursday,Friday' },   # runs only on the week's last trading day
    @{ Name = 'Rotation monthly';      Job = 'rotate';  At = '9:35AM';  Days = $null }   # daily trigger; the script only acts on days 1-5
)
foreach ($j in $jobs) {
    $path = '\SwingBot\'
    if ($Remove) { Unregister-ScheduledTask -TaskName $j.Name -TaskPath $path -Confirm:$false -ErrorAction SilentlyContinue; continue }
    if ($j.Script) { $psArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $PSScriptRoot $j.Script)`"" }
    else { $psArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Job $($j.Job)" }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $psArgs
    if ($j.Days) { $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek ($j.Days -split ',') -At $j.At }
    else { $trigger = New-ScheduledTaskTrigger -Daily -At $j.At }
    if ($j.Script -and $WithUnlockTrigger) {   # also start when you unlock the PC, in case the 8:20 wake-up was missed
        $unlock = New-CimInstance -ClientOnly -CimClass (Get-CimClass -ClassName MSFT_TaskSessionStateChangeTrigger -Namespace Root/Microsoft/Windows/TaskScheduler) -Property @{ StateChange = 8; Enabled = $true }
        $trigger = @($trigger, $unlock)
    }
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
    # Interactive logon: needs you signed in, because the tastytrade credentials live in YOUR Credential Manager.
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    if ($j.Script) { $settings.ExecutionTimeLimit = 'PT9H' }   # the keep-awake job runs most of the day
    Register-ScheduledTask -TaskName $j.Name -TaskPath $path -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Output "Registered $path$($j.Name)  ($(if ($j.Script) { $j.Script } else { $j.Job }) at $($j.At))"
}
if ($Remove) { Write-Output 'SwingBot tasks removed.' }
