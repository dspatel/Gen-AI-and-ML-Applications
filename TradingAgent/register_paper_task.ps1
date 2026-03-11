param(
    [string]$TaskName = "TradingAgent-ORB-Paper"
)

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runScript = Join-Path $repoRoot "run_paper_scheduled.ps1"

if (-not (Test-Path $runScript)) {
    throw "run_paper_scheduled.ps1 not found at $runScript"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
$triggerDaily = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At 8:30AM

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $triggerDaily `
        -Settings $settings `
        -Description "ORB paper trading session launcher (weekday 8:30 CT window runner)" `
        -Force `
        -ErrorAction Stop | Out-Null
    Write-Host "Registered scheduled task: $TaskName"
}
catch {
    Write-Error "Failed to register scheduled task. Try running PowerShell as Administrator. Details: $($_.Exception.Message)"
    exit 1
}
