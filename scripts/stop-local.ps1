$ErrorActionPreference = "Continue"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$localDir = Join-Path $repoRoot ".local"

foreach ($name in @("frontend", "backend")) {
    $pidFile = Join-Path $localDir "$name.pid"
    if (-not (Test-Path $pidFile)) {
        continue
    }

    $processId = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($processId) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($process) {
            Write-Host "Stopping $name process $processId"
            Stop-Process -Id $processId -Force
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

Write-Host "NOVA local dev stop requested."
