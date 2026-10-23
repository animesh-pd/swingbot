# Keeps Windows from sleeping while the market is open, then lets go. Started by Task Scheduler on weekday mornings.
# It asks Windows "system required" (the same request a video player makes), so it needs no admin rights and changes
# no power settings: when this script ends, sleep works exactly as before.
#   -UntilET 16:05   stop at this US Eastern time (default 4:20 PM ET)     -Minutes N   run N minutes (testing only)
param([string]$UntilET = '16:20', [double]$Minutes = 0)
$root = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Force (Join-Path $root 'logs') | Out-Null
$log = Join-Path $root ('logs\' + (Get-Date).ToString('yyyy-MM-dd') + '.log')
function Log($m) { Add-Content -Path $log -Value ('[{0}] keep-awake: {1}' -f (Get-Date -Format 'HH:mm:ss'), $m) }

Add-Type -Namespace Win32 -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
$ES_CONTINUOUS = [uint32]2147483648; $ES_SYSTEM_REQUIRED = [uint32]1

$et = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date), 'Eastern Standard Time')
if ($Minutes -le 0) {   # weekends and market holidays: nothing to stay awake for
    $py = if ($env:SWINGBOT_PYTHON) { $env:SWINGBOT_PYTHON } else { Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe' }
    $reason = & $py (Join-Path $root 'run.py') can-run keepawake 2>&1
    if ($LASTEXITCODE -ne 0) { Log "skipped: $reason"; exit 0 }
}
if ($Minutes -gt 0) { $end = (Get-Date).AddMinutes($Minutes) }
else {
    $h, $m = $UntilET -split ':'
    $endET = $et.Date.AddHours([int]$h).AddMinutes([int]$m)
    $end = (Get-Date) + ($endET - $et)
}
if ((Get-Date) -ge $end) { Log "skipped: already past $UntilET ET"; exit 0 }

[void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
Log ("on until {0:HH:mm} local ({1} ET)" -f $end, $UntilET)
try { while ((Get-Date) -lt $end) { Start-Sleep -Seconds 30 } }
finally { [void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS); Log 'off: sleep allowed again' }
