[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$composeFile = Join-Path $repoRoot "deploy/split/docker-compose.app.yml"
$envFile = Join-Path $repoRoot "deploy/split/.env.app"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not available in PATH."
}
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing $envFile. Run Initialize-SplitConfig.ps1 -Scope App first."
}

$composeArgs = @(
    "compose", "--project-name", "yuxi-app",
    "--project-directory", $repoRoot,
    "--env-file", $envFile,
    "-f", $composeFile
)

& docker @composeArgs config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Application Compose validation failed."
}
Write-Host "Application Compose configuration is valid." -ForegroundColor Green

& docker @composeArgs ps
if ($LASTEXITCODE -ne 0) {
    throw "Failed to read application service status."
}

$portLine = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^APP_HTTP_PORT=(\d+)$' } | Select-Object -Last 1
$port = if ($portLine) { $portLine.Split("=", 2)[1] } else { "8080" }
$bindLine = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^APP_BIND_HOST=(.+)$' } | Select-Object -Last 1
$bindHost = if ($bindLine) { $bindLine.Split("=", 2)[1].Trim() } else { "127.0.0.1" }
$healthHost = if ($bindHost -in @("0.0.0.0", "::")) { "127.0.0.1" } else { $bindHost }
$healthUri = "http://${healthHost}:${port}/api/system/health"
Invoke-RestMethod -Method Get -Uri $healthUri -TimeoutSec 15 | Out-Null
Write-Host "Application health check passed: $healthUri" -ForegroundColor Green
