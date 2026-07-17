[CmdletBinding()]
param(
    [switch]$NoBuild
)

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
    throw "Application Compose validation failed. Check .env.app."
}

$upArgs = @("up", "-d")
if (-not $NoBuild) {
    $upArgs += "--build"
}

& docker @composeArgs @upArgs
if ($LASTEXITCODE -ne 0) {
    throw "Failed to start application services."
}

& docker @composeArgs ps
