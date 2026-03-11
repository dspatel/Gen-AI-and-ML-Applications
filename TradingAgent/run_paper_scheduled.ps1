$existing = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match '^python(\.exe)?$' -and $_.CommandLine -match '--mode paper_live' }

if ($existing) {
    $pids = ($existing | Select-Object -ExpandProperty ProcessId) -join ','
    Write-Host "[ORB_PAPER] already running (pid=$pids). Skip duplicate start."
    exit 0
}

python -u -m agent.main --mode paper_live
