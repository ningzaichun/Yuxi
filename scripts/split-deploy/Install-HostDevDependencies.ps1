[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$backendPath = Join-Path $repoRoot "backend"
$webPath = Join-Path $repoRoot "web"

foreach ($command in @("uv", "node", "pnpm")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command is not available in PATH. Install the host development prerequisites first."
    }
}

$nodeVersion = (& node --version).Trim().TrimStart("v")
$nodeMajor = [int]($nodeVersion.Split(".")[0])
if ($nodeMajor -lt 24) {
    throw "Node.js 24+ is required to match docker/web.Dockerfile. Current version: $nodeVersion"
}

Write-Host "Syncing Python 3.13 backend dependencies..." -ForegroundColor Cyan
& uv sync --project $backendPath --python 3.13 --group dev --group test
if ($LASTEXITCODE -ne 0) {
    throw "Backend dependency installation failed."
}

Write-Host "Installing frontend dependencies..." -ForegroundColor Cyan
Push-Location $webPath
try {
    & pnpm install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend dependency installation failed."
    }
} finally {
    Pop-Location
}

Write-Host "Host development dependencies are ready." -ForegroundColor Green
