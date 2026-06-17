param(
    [int]$Port = 5173,
    [int]$BackendPort = 8000,
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$frontendDir = Join-Path $repoRoot "frontend"
$localDir = Join-Path $repoRoot ".local"
New-Item -ItemType Directory -Path $localDir -Force | Out-Null

Set-Location $frontendDir

# Force local same-origin proxy behavior. This prevents a copied .env file with
# a production VITE_BACKEND_URL from hijacking local development.
$env:VITE_BACKEND_URL = ""
$env:VITE_BACKEND_PORT = "$BackendPort"

Write-Host "Starting NOVA frontend on http://$HostAddress`:$Port"
Write-Host "Proxying /api and /ws to http://127.0.0.1:$BackendPort"
$process = Start-Process -FilePath npm.cmd -ArgumentList @(
    "run",
    "dev",
    "--",
    "--host",
    $HostAddress,
    "--port",
    "$Port",
    "--strictPort"
) -NoNewWindow -PassThru
Set-Content -Path (Join-Path $localDir "frontend.pid") -Value $process.Id
Wait-Process -Id $process.Id
