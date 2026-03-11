param(
    [string]$TaskName = "TradingAgent-ORB-Paper"
)

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
Write-Host "Removed scheduled task: $TaskName"
