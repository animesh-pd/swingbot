# Runs one SwingBot job from Windows Task Scheduler, with guards and a log file (..\logs\YYYY-MM-DD.log).
#   -Job scan | monitor | rotate      -Force = ignore the market-hours guard (manual testing only)
param(
    [Parameter(Mandatory)][ValidateSet('scan', 'monitor', 'rotate')][string]$Job,
    [switch]$Force
)
$root = Split-Path -Parent $PSScriptRoot
# Set SWINGBOT_PYTHON to your python.exe, or edit the default below. (Plain `python` can be a broken Microsoft Store stub.)
$py = if ($env:SWINGBOT_PYTHON) { $env:SWINGBOT_PYTHON } else { Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe' }
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ((Get-Date).ToString('yyyy-MM-dd') + '.log')
function Log($m) { Add-Content -Path $log -Value ('[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $m) }

# A run that fires late (laptop was asleep) must not trade on after-hours prices.
$et = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date), 'Eastern Standard Time')
$mins = $et.Hour * 60 + $et.Minute
if (-not $Force) {
    if ($et.DayOfWeek -in 'Saturday', 'Sunday') { Log "$Job skipped: weekend"; exit 0 }
    if ($mins -lt (9 * 60 + 35) -or $mins -gt (15 * 60 + 55)) { Log "$Job skipped: US market closed (ET $($et.ToString('HH:mm')))"; exit 0 }
    if ($Job -eq 'rotate' -and (Get-Date).Day -gt 5) { Log 'rotate skipped: only runs on days 1-5 of the month'; exit 0 }
}

Log "$Job starting"
$env:PYTHONUTF8 = '1'
Set-Location $root
cmd /c "`"$py`" run.py $Job >> `"$log`" 2>&1"
Log "$Job finished (exit code $LASTEXITCODE)"
exit $LASTEXITCODE
