param(
    [int]$Port = 8000,
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendDir = Join-Path $repoRoot "backend"
$localDir = Join-Path $repoRoot ".local"
New-Item -ItemType Directory -Path $localDir -Force | Out-Null

Set-Location $backendDir

# Local-only overrides. Keep backend/.env intact, but avoid Neon/network
# dependencies and keep all trading gates safe for paper-mode development.
$env:APP_ENV = "local"
$env:DATABASE_URL = "sqlite:///auth_local.db"
$env:AUTH_REQUIRED = "false"
$env:SESSION_COOKIE_SECURE = "false"
$env:BACKEND_PUBLIC_BASE_URL = "http://localhost:$Port"
$env:FRONTEND_ORIGIN = "http://localhost:5173"
$env:FRONTEND_URL = "http://localhost:5173"
$env:DHAN_MODE = "MOCK"
$env:DHAN_READ_ONLY_REAL_DATA = "true"
$env:PAPER_MODE_ENABLED = "true"
$env:ENABLE_LIVE_ORDERS = "false"
$env:EXECUTION_NODE_ROUTING_ENABLED = "false"
$env:WEBHOOK_TRADING_ENABLED = "false"
$env:STRATEGY_JOB_WORKER_ENABLED = "false"

Write-Host "Starting NOVA backend on http://$HostAddress`:$Port"
Write-Host "Local DB: backend/auth_local.db"
$process = Start-Process -FilePath python.exe -ArgumentList @(
    "-m",
    "uvicorn",
    "app.main:app",
    "--host",
    $HostAddress,
    "--port",
    "$Port"
) -NoNewWindow -PassThru
Set-Content -Path (Join-Path $localDir "backend.pid") -Value $process.Id
Wait-Process -Id $process.Id
