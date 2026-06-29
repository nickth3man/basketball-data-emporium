param(
    [string]$ApiUrl = "http://127.0.0.1:8765/api/status",
    [switch]$AllowUnverified
)

$ErrorActionPreference = "Stop"

try {
    $status = Invoke-RestMethod -Method Get -Uri $ApiUrl -Headers @{ Accept = "application/json" }
}
catch {
    Write-Error "Could not read API status from $ApiUrl. Start the backend before running this gate. $($_.Exception.Message)"
    exit 1
}

Write-Host "ok=$($status.ok)"
Write-Host "data_state=$($status.data_state)"
Write-Host "data_state_reason=$($status.data_state_reason)"
Write-Host "data_verified=$($status.data_verified)"
Write-Host "latest_pipeline_run_id=$($status.latest_pipeline_run_id)"
Write-Host "latest_pipeline_status=$($status.latest_pipeline_status)"
Write-Host "latest_dq_status=$($status.latest_dq_status)"

if (-not $status.ok) {
    Write-Error "API status is not ok."
    exit 1
}

if (-not $AllowUnverified -and -not $status.data_verified) {
    Write-Error "Data is not verified. Use -AllowUnverified only for local exploratory work, not release checks."
    exit 1
}
