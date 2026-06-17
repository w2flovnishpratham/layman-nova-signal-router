param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$localDir = Join-Path $repoRoot ".local"
New-Item -ItemType Directory -Path $localDir -Force | Out-Null

$backendScript = Join-Path $PSScriptRoot "run-backend-local.ps1"
$frontendScript = Join-Path $PSScriptRoot "run-frontend-local.ps1"

Remove-Item (Join-Path $localDir "backend.pid") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $localDir "frontend.pid") -Force -ErrorAction SilentlyContinue

Write-Host "Starting NOVA local dev..."
Write-Host "Backend:  http://$HostAddress`:$BackendPort/api/health"
Write-Host "Frontend: http://$HostAddress`:$FrontendPort"
Write-Host "Press Ctrl+C to stop both processes."

$backendRunner = Start-Process -FilePath powershell.exe -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $backendScript,
    "-Port",
    "$BackendPort",
    "-HostAddress",
    $HostAddress
) -NoNewWindow -PassThru

$frontendRunner = Start-Process -FilePath powershell.exe -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $frontendScript,
    "-Port",
    "$FrontendPort",
    "-BackendPort",
    "$BackendPort",
    "-HostAddress",
    $HostAddress
) -NoNewWindow -PassThru

try {
    while ($true) {
        $backendRunner.Refresh()
        $frontendRunner.Refresh()

        if ($backendRunner.HasExited) {
            throw "Backend runner exited with code $($backendRunner.ExitCode)."
        }
        if ($frontendRunner.HasExited) {
            throw "Frontend runner exited with code $($frontendRunner.ExitCode)."
        }

        Start-Sleep -Seconds 2
    }
}
finally {
    foreach ($pidFileName in @("frontend.pid", "backend.pid")) {
        $pidFile = Join-Path $localDir $pidFileName
        if (-not (Test-Path $pidFile)) {
            continue
        }
        $processId = Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($processId) {
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    }

    Stop-Process -Id $frontendRunner.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $backendRunner.Id -Force -ErrorAction SilentlyContinue
    Write-Host "NOVA local dev stopped."
}
