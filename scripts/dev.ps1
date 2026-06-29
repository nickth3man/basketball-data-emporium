param(
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 8765,
    [string]$FrontendHost = "127.0.0.1",
    [int]$FrontendPort = 3000
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendDir = Join-Path $repoRoot "backend"
$frontendDir = Join-Path $repoRoot "frontend"

function Get-PortOwner {
    param([int]$Port)
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($connections) {
        return ($connections | Select-Object -First 1).OwningProcess
    }
    return $null
}

function Assert-PortAvailable {
    param([int]$Port)
    $owner = Get-PortOwner -Port $Port
    if ($owner) {
        $process = Get-Process -Id $owner -ErrorAction SilentlyContinue
        $name = if ($process) { $process.ProcessName } else { "unknown" }
        throw "Port $Port is already owned by PID $owner ($name). Stop that process before running scripts/dev.ps1."
    }
}

Assert-PortAvailable -Port $BackendPort
Assert-PortAvailable -Port $FrontendPort

$backend = Start-Process -FilePath "uv" `
    -ArgumentList @("run", "basketball-data-emporium", "serve", "--host", $BackendHost, "--port", "$BackendPort") `
    -WorkingDirectory $backendDir `
    -PassThru `
    -WindowStyle Hidden

$frontend = Start-Process -FilePath "npm" `
    -ArgumentList @("run", "dev", "--", "--hostname", $FrontendHost, "--port", "$FrontendPort") `
    -WorkingDirectory $frontendDir `
    -PassThru `
    -WindowStyle Hidden

try {
    Write-Host "Backend:  http://$BackendHost`:$BackendPort"
    Write-Host "Frontend: http://$FrontendHost`:$FrontendPort"
    Write-Host "Press Ctrl+C to stop both processes."
    Wait-Process -Id $backend.Id, $frontend.Id
}
finally {
    foreach ($child in @($backend, $frontend)) {
        if ($child -and -not $child.HasExited) {
            Stop-Process -Id $child.Id -Force
        }
    }
}
