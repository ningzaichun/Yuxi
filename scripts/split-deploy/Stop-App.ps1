[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$composeFile = Join-Path $repoRoot "deploy/split/docker-compose.app.yml"
$envFile = Join-Path $repoRoot "deploy/split/.env.app"

if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing $envFile."
}

$composeArgs = @(
    "compose", "--project-name", "yuxi-app",
    "--project-directory", $repoRoot,
    "--env-file", $envFile,
    "-f", $composeFile
)

# Intentionally omit --volumes so application saves and model data are preserved.
& docker @composeArgs down --remove-orphans
if ($LASTEXITCODE -ne 0) {
    throw "Failed to stop application services."
}
