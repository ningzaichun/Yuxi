[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$composeFile = Join-Path $repoRoot "deploy/split/docker-compose.infra.yml"
$envFile = Join-Path $repoRoot "deploy/split/.env.infra"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not available in PATH."
}
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing $envFile. Run Initialize-SplitConfig.ps1 first."
}

$composeArgs = @(
    "compose", "--project-name", "yuxi-infra",
    "--project-directory", $repoRoot,
    "--env-file", $envFile,
    "-f", $composeFile
)

& docker @composeArgs config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Infrastructure Compose validation failed."
}
Write-Host "Infrastructure Compose configuration is valid." -ForegroundColor Green

& docker @composeArgs ps
if ($LASTEXITCODE -ne 0) {
    throw "Failed to read infrastructure service status."
}
