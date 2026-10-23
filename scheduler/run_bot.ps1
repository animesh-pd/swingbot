# Runs one SwingBot job from Windows Task Scheduler, with guards and a log file (..\logs\YYYY-MM-DD.log).
#   -Job scan | monitor | rotate      -Force = ignore the market-hours guard (manual testing only)
param(
    [Parameter(Mandatory)][ValidateSet('scan', 'monitor', 'rotate', 'weekly')][string]$Job,
    [switch]$Force
)
$root = Split-Path -Parent $PSScriptRoot
# Set SWINGBOT_PYTHON to your python.exe, or edit the default below. (Plain `python` can be a broken Microsoft Store stub.)
$py = if ($env:SWINGBOT_PYTHON) { $env:SWINGBOT_PYTHON } else { Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe' }
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ((Get-Date).ToString('yyyy-MM-dd') + '.log')
function Log($m) { Add-Content -Path $log -Value ('[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $m) }

# One calendar for every job (holidays, half days, market hours, day-of-month rules): `run.py can-run`.
# A run that fires late (laptop was asleep) must not trade on stale or after-hours prices.
if (-not $Force) {
    $reason = & $py (Join-Path $root 'run.py') can-run $Job 2>&1
    if ($LASTEXITCODE -ne 0) { Log "$Job skipped: $reason"; exit 0 }
}

Log "$Job starting"
$env:PYTHONUTF8 = '1'
Set-Location $root
cmd /c "`"$py`" run.py $Job >> `"$log`" 2>&1"
Log "$Job finished (exit code $LASTEXITCODE)"
exit $LASTEXITCODE
