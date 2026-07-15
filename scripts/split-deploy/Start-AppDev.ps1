[CmdletBinding()]
param(
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$composeFile = Join-Path $repoRoot "deploy/split/docker-compose.app.dev.yml"
$envFile = Join-Path $repoRoot "deploy/split/.env.app.dev"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not available in PATH."
}
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing $envFile. Run Initialize-SplitConfig.ps1 first."
}

$composeArgs = @(
    "compose", "--project-name", "yuxi-app-dev",
    "--project-directory", $repoRoot,
    "--env-file", $envFile,
    "-f", $composeFile
)

& docker @composeArgs config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Application Compose validation failed. Check .env.app.dev."
}

$upArgs = @("up", "-d")
if (-not $NoBuild) {
    $upArgs += "--build"
}

& docker @composeArgs @upArgs
if ($LASTEXITCODE -ne 0) {
    throw "Failed to start local application services."
}

& docker @composeArgs ps
